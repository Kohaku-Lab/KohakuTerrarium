"""Client connector — connects out to a Laboratory host, runs envelope loops.

A :class:`ClientConnector` instance owns:

- the outbound :class:`~kohakuterrarium.laboratory._internal.transport_base.Connection`
- the Hello/Welcome handshake state (assigned ``client_id``, host's
  declared capabilities, etc.)
- read / write / heartbeat tasks once connected
- an outbound :class:`BoundedSendBuffer` so callers can ``send()``
  without blocking on slow I/O
- auto-reconnect with exponential backoff when the transport drops

Permanent rejections (auth failed, protocol mismatch) raise
:class:`AuthFailedError` or :class:`ProtocolMismatchError` from
:meth:`start` and stop the connector. Transient failures (transport
refused, peer reset) trigger reconnect.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from kohakuterrarium.laboratory.config import ClientConfig
from kohakuterrarium.laboratory._internal.app import (
    AppMessageError,
    ExtensionHandler,
    build_app_envelope,
    new_request_id,
    parse_app_envelope,
)
from kohakuterrarium.laboratory._internal.backpressure import (
    BoundedSendBuffer,
)
from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeDecodeError,
    EnvelopeKind,
)
from kohakuterrarium.laboratory._internal.protocol import (
    HOST_NODE_ID,
    LAB_PROTOCOL_VERSION,
    HelloPayload,
    ProtocolError,
    build_hello,
    parse_reject,
    parse_welcome,
)
from kohakuterrarium.laboratory._internal.transport_base import (
    Connection,
    ConnectionClosed,
    ConnectionRefused,
    Transport,
)
from kohakuterrarium.utils.logging import get_logger

_log = get_logger(__name__)


class ClientError(Exception):
    """Base class for ClientConnector errors."""


class AuthFailedError(ClientError):
    """Raised when the host rejects the Hello with ``reason='auth_failed'``."""


class ProtocolMismatchError(ClientError):
    """Raised when the host rejects with ``reason='protocol_mismatch'``."""


class NameConflictError(ClientError):
    """Raised when the host rejects with ``reason='name_conflict'``."""


class RequestTimeoutError(TimeoutError):
    """Raised by :meth:`ClientConnector.request` when no response arrives."""


class RequestAbortedError(RequestTimeoutError):
    """Raised when a pending request cannot complete because the target
    client disconnected or the host stopped.

    Subclasses :class:`RequestTimeoutError` so legacy ``except`` blocks
    continue to handle the case; new code may catch the more specific
    subclass to distinguish "wait timed out" from "link gone before
    response".
    """


InboundHandler = Callable[[Envelope], Awaitable[None]]


class ClientConnector:
    """Outbound client connector to a Laboratory host.

    Lifecycle:

    .. code-block:: python

        client = ClientConnector(config, transport)
        client.on_envelope(my_handler)
        await client.start()
        ...
        await client.send(envelope)
        ...
        await client.stop()
    """

    def __init__(
        self,
        config: ClientConfig,
        transport: Transport,
        *,
        framework_version: str = "",
    ) -> None:
        self._config = config
        self._transport = transport
        self._framework_version = framework_version
        self._client_id: str | None = None
        self._connection: Connection | None = None
        self._send_buffer = BoundedSendBuffer(maxsize=config.backpressure_buffer_size)
        self._inbound_handlers: list[InboundHandler] = []
        self._stopped = False
        self._main_task: asyncio.Task | None = None
        self._read_task: asyncio.Task | None = None
        self._write_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._connected_event = asyncio.Event()
        self._disconnect_event = asyncio.Event()
        self._first_connect_done = asyncio.Event()
        self._first_connect_error: Exception | None = None
        self._app_extensions: dict[str, ExtensionHandler] = {}
        self._pending_requests: dict[str, asyncio.Future[Any]] = {}
        # Callbacks invoked when the host link goes away.  Used by
        # :class:`StreamDemux` (and similar consumers) so streams whose
        # producer is the host get drained on disconnect rather than
        # hanging on ``queue.get()``.  Each callback is sync, receives
        # the departed node id (``HOST_NODE_ID`` here), and runs in the
        # client's tear-down path.
        self._disconnect_callbacks: list[Callable[[str], None]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def client_id(self) -> str | None:
        """Assigned client id from the host's Welcome, or ``None``."""
        return self._client_id

    @property
    def is_connected(self) -> bool:
        return self._connected_event.is_set()

    def on_envelope(self, handler: InboundHandler) -> None:
        """Register a handler called for every inbound envelope."""
        self._inbound_handlers.append(handler)

    async def start(self) -> None:
        """Start the connector. Returns after the first successful handshake.

        On a permanent rejection (auth, protocol mismatch, name conflict),
        the matching :class:`ClientError` subclass is raised and the
        connector stops.
        """
        if self._main_task is not None:
            raise RuntimeError("ClientConnector already started")
        _log.info(
            "lab client starting",
            client_name=self._config.client_name,
            host_url=self._config.host_url,
        )
        self._main_task = asyncio.create_task(self._main_loop())
        await self._first_connect_done.wait()
        if self._first_connect_error is not None:
            err = self._first_connect_error
            _log.error(
                "lab client failed initial connect",
                client_name=self._config.client_name,
                host_url=self._config.host_url,
                error=str(err),
            )
            await self.stop()
            raise err

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        _log.info(
            "lab client stopping",
            client_name=self._config.client_name,
            client_id=self._client_id,
        )
        if self._main_task is not None:
            self._main_task.cancel()
        await self._tear_down_connection()
        if self._main_task is not None:
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass

    async def send(self, env: Envelope, *, wait: bool = True) -> None:
        """Enqueue an envelope for transmission to the host."""
        await self._send_buffer.put(env, wait=wait)

    # ------------------------------------------------------------------
    # Extension API
    # ------------------------------------------------------------------

    def register_app_extension(
        self,
        namespace: str,
        handler: ExtensionHandler,
    ) -> None:
        """Register an APP extension handler for a namespace.

        Each namespace may have at most one client-side handler; re-registering
        raises :class:`ValueError`.
        """
        if namespace in self._app_extensions:
            raise ValueError(
                f"APP extension for namespace {namespace!r} already registered"
            )
        self._app_extensions[namespace] = handler

    def unregister_app_extension(self, namespace: str) -> bool:
        """Remove a registered APP extension. Returns whether one existed."""
        return self._app_extensions.pop(namespace, None) is not None

    def on_node_disconnect(self, callback: Callable[[str], None]) -> None:
        """Register a sync callback fired when the host link drops.

        Mirrors :meth:`HostEngine.on_node_disconnect`; used by stream
        demuxes installed client-side so their queues are drained when
        the host disappears.  The callback receives ``HOST_NODE_ID`` —
        the only "node" a client can lose a direct connection to.
        Exceptions are logged and swallowed.
        """
        self._disconnect_callbacks.append(callback)

    async def notify(
        self,
        *,
        to_node: str,
        namespace: str,
        type: str,
        body: Any = None,
    ) -> None:
        """Send a fire-and-forget APP message; no response expected."""
        await self.send(
            build_app_envelope(
                from_node=self._client_id or "",
                to_node=to_node,
                namespace=namespace,
                type=type,
                body=body,
            )
        )

    async def request(
        self,
        *,
        to_node: str,
        namespace: str,
        type: str,
        body: Any = None,
        timeout: float = 30.0,
    ) -> Any:
        """Send an APP message and await the response.

        Raises :class:`RequestTimeoutError` if no response arrives within
        ``timeout`` seconds.
        """
        request_id = new_request_id()
        loop = asyncio.get_event_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending_requests[request_id] = future
        env = build_app_envelope(
            from_node=self._client_id or "",
            to_node=to_node,
            namespace=namespace,
            type=type,
            body=body,
            request_id=request_id,
        )
        _log.debug(
            "APP request outbound",
            to_node=to_node,
            namespace=namespace,
            msg_type=type,
            request_id=request_id,
        )
        try:
            await self.send(env)
            result = await asyncio.wait_for(future, timeout=timeout)
            _log.debug(
                "APP response received",
                from_node=to_node,
                namespace=namespace,
                msg_type=type,
                request_id=request_id,
            )
            return result
        except asyncio.TimeoutError as exc:
            # ``RequestAbortedError`` is a TimeoutError subclass — when
            # ``_tear_down_connection`` ``set_exception``s the future
            # with it, ``wait_for`` propagates it through this same
            # except.  Re-raise typed errors as-is rather than wrapping
            # them back into a plain RequestTimeoutError.
            if isinstance(exc, RequestTimeoutError):
                raise
            _log.warning(
                "client APP request timed out",
                to_node=to_node,
                namespace=namespace,
                msg_type=type,
                request_id=request_id,
                timeout=timeout,
            )
            raise RequestTimeoutError(
                f"no response for {namespace}/{type} (request_id={request_id})"
                f" within {timeout}s"
            ) from exc
        finally:
            self._pending_requests.pop(request_id, None)

    # ------------------------------------------------------------------
    # Main lifecycle loop
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        backoff = self._config.reconnect_initial_delay_seconds
        first_attempt = True
        reconnect_attempt = 0
        try:
            while not self._stopped:
                # Always start each connect attempt with the disconnect
                # event cleared. The previous tear_down may have set it
                # again via the write_task's finally; clearing here means
                # the new connection's wait() blocks correctly.
                self._disconnect_event.clear()
                try:
                    await self._connect_once()
                    if reconnect_attempt > 0:
                        _log.info(
                            "lab client reconnected",
                            client_name=self._config.client_name,
                            attempts=reconnect_attempt,
                        )
                        reconnect_attempt = 0
                    backoff = self._config.reconnect_initial_delay_seconds
                    if first_attempt:
                        first_attempt = False
                        self._first_connect_done.set()
                    # Wait until current connection drops.
                    await self._disconnect_event.wait()
                    _log.info(
                        "lab client connection dropped; will reconnect",
                        client_name=self._config.client_name,
                    )
                except (AuthFailedError, ProtocolMismatchError) as exc:
                    # Permanent failures: don't retry.
                    _log.error(
                        "lab client permanent rejection",
                        client_name=self._config.client_name,
                        reason=type(exc).__name__,
                        detail=str(exc),
                    )
                    if first_attempt:
                        self._first_connect_error = exc
                        self._first_connect_done.set()
                    return
                except NameConflictError as exc:
                    # On the very first attempt, name was actually taken
                    # — fatal. On subsequent attempts, this is almost
                    # always a race with the previous session's cleanup
                    # on the host; retry with backoff.
                    if first_attempt:
                        _log.error(
                            "lab client rejected: name conflict",
                            client_name=self._config.client_name,
                        )
                        self._first_connect_error = exc
                        self._first_connect_done.set()
                        return
                    _log.debug(
                        "name_conflict on reconnect (likely cleanup race); retrying"
                    )
                except (ConnectionRefused, OSError, ConnectionClosed) as exc:
                    _log.debug("transient connect error: %s", exc)
                except Exception:
                    _log.exception("unexpected error in client main loop")
                if self._stopped:
                    break
                await self._tear_down_connection()
                # Wait, then retry.
                reconnect_attempt += 1
                _log.warning(
                    "lab client reconnect attempt",
                    client_name=self._config.client_name,
                    attempt=reconnect_attempt,
                    backoff_seconds=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(
                    backoff * 2,
                    self._config.reconnect_max_delay_seconds,
                )
        finally:
            if first_attempt and not self._first_connect_done.is_set():
                self._first_connect_done.set()

    async def _connect_once(self) -> None:
        conn = await self._transport.connect(self._config.host_url)
        self._connection = conn

        # Send Hello
        hello = HelloPayload(
            protocol_version=LAB_PROTOCOL_VERSION,
            framework_version=self._framework_version,
            client_name=self._config.client_name,
            token=self._config.token,
            capabilities=self._config.capabilities,
        )
        try:
            await conn.send_frame(build_hello(hello).encode())
        except ConnectionClosed as exc:
            raise ConnectionClosed(f"hello send failed: {exc}") from exc
        _log.debug(
            "HELLO sent",
            client_name=self._config.client_name,
            protocol_version=LAB_PROTOCOL_VERSION,
        )

        # Receive Welcome or Reject
        try:
            raw = await conn.recv_frame()
        except ConnectionClosed as exc:
            raise ConnectionClosed(f"welcome recv failed: {exc}") from exc
        try:
            env = Envelope.decode(raw)
        except EnvelopeDecodeError as exc:
            raise ClientError(f"malformed handshake response: {exc}") from exc

        match env.kind:
            case EnvelopeKind.WELCOME:
                try:
                    welcome = parse_welcome(env)
                except ProtocolError as exc:
                    raise ClientError(f"invalid welcome: {exc}") from exc
                self._client_id = welcome.assigned_client_id
                self._connected_event.set()
                _log.info(
                    "lab client registered",
                    client_name=self._config.client_name,
                    assigned_client_id=welcome.assigned_client_id,
                    host_node_id=welcome.host_node_id,
                    host_protocol=welcome.protocol_version,
                    host_framework_version=welcome.framework_version,
                )
                self._read_task = asyncio.create_task(self._read_loop(conn))
                self._write_task = asyncio.create_task(self._write_loop(conn))
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(conn))
                return
            case EnvelopeKind.CONTROL:
                try:
                    reject = parse_reject(env)
                except ProtocolError as exc:
                    raise ClientError(f"malformed control after Hello: {exc}") from exc
                await conn.close()
                self._connection = None
                match reject.reason:
                    case "auth_failed":
                        raise AuthFailedError(reject.detail or "auth failed")
                    case "protocol_mismatch":
                        raise ProtocolMismatchError(
                            reject.detail or "protocol mismatch"
                        )
                    case "name_conflict":
                        raise NameConflictError(reject.detail or "name conflict")
                    case _:
                        raise ClientError(
                            f"host rejected: {reject.reason} ({reject.detail})"
                        )
            case _:
                raise ClientError(
                    f"unexpected envelope kind in handshake response: "
                    f"{env.kind.value}"
                )

    async def _tear_down_connection(self) -> None:
        self._connected_event.clear()
        # Notify disconnect listeners (e.g. ``StreamDemux``) BEFORE
        # failing pending requests so streams drain first.  Sync,
        # best-effort: a buggy callback can't prevent tear-down.
        for cb in list(self._disconnect_callbacks):
            try:
                cb(HOST_NODE_ID)
            except Exception:
                _log.exception(
                    "client disconnect callback raised",
                )
        # Fail any in-flight ``request()`` callers with a structured
        # abort instead of leaving them blocked until ``asyncio.wait_for``
        # times out.  Iterates a snapshot because ``request()``'s
        # ``finally`` clause pops entries on resolution.
        aborted = 0
        for request_id, future in list(self._pending_requests.items()):
            if not future.done():
                future.set_exception(
                    RequestAbortedError(
                        f"host connection torn down before responding to "
                        f"request {request_id}"
                    )
                )
                aborted += 1
        if aborted:
            _log.info(
                "lab client aborted pending requests on tear-down",
                client_name=self._config.client_name,
                aborted_requests=aborted,
            )
        for task in (self._read_task, self._write_task, self._heartbeat_task):
            if task is not None and not task.done():
                task.cancel()
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception:
                pass
            self._connection = None
        for task in (self._read_task, self._write_task, self._heartbeat_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._read_task = None
        self._write_task = None
        self._heartbeat_task = None

    # ------------------------------------------------------------------
    # Per-connection loops
    # ------------------------------------------------------------------

    async def _read_loop(self, conn: Connection) -> None:
        try:
            while not self._stopped:
                try:
                    raw = await conn.recv_frame()
                except ConnectionClosed:
                    break
                try:
                    env = Envelope.decode(raw)
                except EnvelopeDecodeError as exc:
                    _log.warning("client got malformed envelope: %s", exc)
                    continue
                await self._dispatch_inbound(env)
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception(
                "client read loop crashed for %s",
                self._config.client_name,
            )
        finally:
            self._disconnect_event.set()

    async def _write_loop(self, conn: Connection) -> None:
        try:
            while not self._stopped:
                env = await self._send_buffer.get()
                try:
                    await conn.send_frame(env.encode())
                except ConnectionClosed:
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception(
                "client write loop crashed for %s",
                self._config.client_name,
            )
        finally:
            self._disconnect_event.set()

    async def _heartbeat_loop(self, conn: Connection) -> None:
        interval = self._config.heartbeat_interval_seconds
        _log.info(
            "heartbeat loop started",
            client_name=self._config.client_name,
            interval_seconds=interval,
        )
        try:
            while not self._stopped:
                await asyncio.sleep(interval)
                if self._client_id is None:
                    continue
                heartbeat = Envelope(
                    from_node=self._client_id,
                    to_node=HOST_NODE_ID,
                    kind=EnvelopeKind.HEARTBEAT,
                    stream_id=0,
                    seq=0,
                )
                try:
                    await conn.send_frame(heartbeat.encode())
                    _log.debug("heartbeat sent", client_name=self._config.client_name)
                except ConnectionClosed:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            _log.debug(
                "heartbeat loop stopped",
                client_name=self._config.client_name,
            )

    async def _dispatch_inbound(self, env: Envelope) -> None:
        # APP envelopes get special-cased: responses resolve pending
        # requests; non-response APP envelopes go to the registered
        # extension. We still fire generic on_envelope handlers for
        # observability and for callers that want raw access.
        if env.kind is EnvelopeKind.APP:
            await self._handle_app(env)
        for handler in list(self._inbound_handlers):
            try:
                await handler(env)
            except Exception:
                _log.exception("inbound handler raised")

    async def _handle_app(self, env: Envelope) -> None:
        try:
            msg = parse_app_envelope(env)
        except AppMessageError as exc:
            _log.warning("client got malformed APP envelope: %s", exc)
            return
        # Response to one of our outstanding requests?
        if msg.in_reply_to is not None:
            future = self._pending_requests.get(msg.in_reply_to)
            if future is not None and not future.done():
                future.set_result(msg.body)
            return
        # Inbound message — dispatch to the namespace's extension.
        handler = self._app_extensions.get(msg.namespace)
        if handler is None:
            _log.debug(
                "no extension registered for APP namespace %r (type %r)",
                msg.namespace,
                msg.type,
            )
            return
        _log.debug(
            "APP request received for handler",
            from_node=msg.sender_node,
            namespace=msg.namespace,
            msg_type=msg.type,
            request_id=msg.request_id,
        )
        # Spawn the handler in a background task so the read loop keeps
        # processing inbound frames.  Nested requests (a handler that
        # issues its own ``request`` and awaits the response) deadlock
        # otherwise: the read loop is blocked on ``await handler``, so
        # the response envelope for the nested request never reaches
        # the pending-futures table.  The dispatcher returns ``None``
        # — the read loop iterates immediately.
        asyncio.create_task(
            self._run_handler_and_reply(msg, handler),
            name=f"app_handler_{msg.namespace}_{msg.type}",
        )

    async def _run_handler_and_reply(
        self,
        msg: Any,
        handler: Any,
    ) -> None:
        """Run a namespace handler and send its result back as a response.

        Spawned as a task by :meth:`_handle_app` so the read loop
        isn't held while the handler awaits.  Errors are logged and
        swallowed (the original sender's request will time out, which
        is the correct surface for an extension failure).
        """
        try:
            result = await handler(msg)
        except Exception:
            _log.exception(
                "APP extension for %r raised handling %r",
                msg.namespace,
                msg.type,
            )
            return
        if msg.request_id is None or result is None:
            return
        await self.send(
            build_app_envelope(
                from_node=self._client_id or "",
                to_node=msg.sender_node,
                namespace=msg.namespace,
                type=msg.type,
                body=result,
                in_reply_to=msg.request_id,
            )
        )


__all__ = [
    "AuthFailedError",
    "ClientConnector",
    "ClientError",
    "NameConflictError",
    "ProtocolMismatchError",
    "RequestAbortedError",
    "RequestTimeoutError",
]
