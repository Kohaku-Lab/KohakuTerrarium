"""Host client connections and route Laboratory protocol envelopes.

The engine owns membership and address registries, isolates each client's
I/O in dedicated tasks, and reaps clients that miss heartbeats. Host-bound
CONTROL and APP envelopes are dispatched locally; other routable envelopes
are resolved to creatures, channel listeners, or client IDs.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from kohakuterrarium.laboratory.config import HostConfig
from kohakuterrarium.laboratory._internal.addressing import AddressDirectory
from kohakuterrarium.laboratory._internal.app import (
    AppMessageError,
    ExtensionHandler,
    build_app_envelope,
    new_request_id,
    parse_app_envelope,
)
from kohakuterrarium.laboratory._internal.app_acl import is_host_only_client_forward
from kohakuterrarium.laboratory._internal.auth import TokenAuth
from kohakuterrarium.laboratory._internal.client import (
    RequestAbortedError,
    RequestTimeoutError,
)
from kohakuterrarium.laboratory._internal.backpressure import (
    BackpressureError,
    BoundedSendBuffer,
)
from kohakuterrarium.laboratory._internal.control import (
    ControlError,
    parse_control,
)
from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeDecodeError,
    EnvelopeKind,
)
from kohakuterrarium.laboratory._internal.membership import Membership
from kohakuterrarium.laboratory._internal.protocol import (
    HOST_NODE_ID,
    LAB_PROTOCOL_VERSION,
    ProtocolError,
    RejectPayload,
    WelcomePayload,
    build_reject,
    build_welcome,
    parse_hello,
    protocol_compatible,
)
from kohakuterrarium.laboratory._internal.transport_base import (
    Connection,
    ConnectionClosed,
    Server,
    Transport,
)
from kohakuterrarium.utils.logging import get_logger

# Custom CONTROL handlers receive the sender, envelope, and fields without the
# discriminator key.
ControlHandler = Callable[
    ["ConnectedClient", Envelope, dict[str, Any]], Awaitable[None]
]

_log = get_logger(__name__)


@dataclass
class ConnectedClient:
    """Per-client state held by the host."""

    client_id: str
    connection: Connection
    capabilities: tuple[str, ...]
    send_buffer: BoundedSendBuffer
    read_task: asyncio.Task | None = None
    write_task: asyncio.Task | None = None
    is_disconnecting: bool = False


class HostEngine:
    """Accept Laboratory clients and route their protocol envelopes."""

    def __init__(
        self,
        config: HostConfig,
        transport: Transport,
        *,
        framework_version: str = "",
    ) -> None:
        self._config = config
        self._transport = transport
        self._framework_version = framework_version
        self._auth = TokenAuth(config.token)
        self._blocked_clients: set[str] = set()
        self._membership = Membership(
            heartbeat_timeout_seconds=config.heartbeat_timeout_seconds
        )
        self._addressing = AddressDirectory()
        self._clients: dict[str, ConnectedClient] = {}
        self._server: Server | None = None
        self._accept_task: asyncio.Task | None = None
        self._reaper_task: asyncio.Task | None = None
        self._stopped = False
        self._reaper_interval_seconds = min(5.0, config.heartbeat_timeout_seconds / 2.0)
        self._control_handlers: dict[str, ControlHandler] = {}
        self._app_extensions: dict[str, ExtensionHandler] = {}
        # Pair pending requests with targets so disconnects fail them immediately.
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._pending_request_targets: dict[str, str] = {}
        # Disconnect callbacks unblock consumers waiting on departed producers.
        self._disconnect_callbacks: list[Callable[[str], None]] = []

    @property
    def is_running(self) -> bool:
        return self._server is not None and not self._stopped

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("HostEngine already started")
        bind_addr = f"{self._config.bind_host}:{self._config.bind_port}"
        self._server = await self._transport.serve(bind_addr)
        self._accept_task = asyncio.create_task(self._accept_loop())
        self._reaper_task = asyncio.create_task(self._reaper_loop())
        _log.info(
            "lab host engine started",
            bind_addr=bind_addr,
            token_source="configured" if self._config.token else "none",
            heartbeat_timeout=self._config.heartbeat_timeout_seconds,
        )

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        _log.info(
            "lab host engine stopping",
            connected_clients=len(self._clients),
            pending_requests=len(self._pending_requests),
        )
        if self._reaper_task is not None:
            self._reaper_task.cancel()
        if self._server is not None:
            await self._server.close()
        if self._accept_task is not None:
            await self._accept_task
        for client in list(self._clients.values()):
            await self._disconnect_client(client, reason="host stopped")
        # Fail requests whose targets disappeared before client teardown began.
        for request_id, future in list(self._pending_requests.items()):
            if not future.done():
                future.set_exception(
                    RequestAbortedError("host stopped before request completed")
                )
        if self._reaper_task is not None:
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
        self._membership.close_subscribers()

    def alive_clients(self) -> set[str]:
        return self._membership.alive()

    def client_capabilities(self, client_id: str) -> tuple[str, ...] | None:
        return self._membership.capabilities(client_id)

    @property
    def membership(self) -> Membership:
        return self._membership

    def set_token(self, token: str) -> None:
        # HostConfig is frozen; handshakes consult the replaceable authenticator.
        self._auth = TokenAuth(token)

    def block_client_id(self, client_id: str) -> None:
        self._blocked_clients.add(client_id)

    def unblock_client_id(self, client_id: str) -> None:
        self._blocked_clients.discard(client_id)

    def blocked_clients(self) -> set[str]:
        return set(self._blocked_clients)

    @property
    def addressing(self) -> AddressDirectory:
        return self._addressing

    def register_control_handler(
        self,
        control_type: str,
        handler: ControlHandler,
    ) -> None:
        """Register one handler for a non-built-in CONTROL discriminator."""
        if control_type in _BUILTIN_CONTROL_TYPES:
            raise ValueError(f"cannot override built-in CONTROL type {control_type!r}")
        if control_type in self._control_handlers:
            raise ValueError(f"CONTROL handler for {control_type!r} already registered")
        self._control_handlers[control_type] = handler

    def register_app_extension(
        self,
        namespace: str,
        handler: ExtensionHandler,
    ) -> None:
        """Register the sole host-side APP handler for a namespace."""
        if namespace in self._app_extensions:
            raise ValueError(
                f"APP extension for namespace {namespace!r} already registered"
            )
        self._app_extensions[namespace] = handler

    def unregister_app_extension(self, namespace: str) -> bool:
        """Remove an APP extension. Returns whether one was registered."""
        return self._app_extensions.pop(namespace, None) is not None

    def unregister_control_handler(self, control_type: str) -> bool:
        """Remove a registered CONTROL handler. Returns whether one existed."""
        return self._control_handlers.pop(control_type, None) is not None

    def on_node_disconnect(self, callback: Callable[[str], None]) -> None:
        """Register a synchronous, best-effort client-disconnect callback."""
        self._disconnect_callbacks.append(callback)

    async def notify(
        self,
        *,
        to_node: str,
        namespace: str,
        type: str,
        body: Any = None,
    ) -> None:
        """Send an APP notification to a connected client."""
        if to_node not in self._clients:
            raise KeyError(f"unknown client {to_node!r}")
        env = build_app_envelope(
            from_node=HOST_NODE_ID,
            to_node=to_node,
            namespace=namespace,
            type=type,
            body=body,
        )
        await self._route_send(env)

    async def request(
        self,
        *,
        to_node: str,
        namespace: str,
        type: str,
        body: Any = None,
        timeout: float = 30.0,
    ) -> Any:
        """Send a host-originated APP request and await its response."""
        if to_node not in self._clients:
            raise KeyError(f"unknown client {to_node!r}")
        request_id = new_request_id()
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_requests[request_id] = future
        self._pending_request_targets[request_id] = to_node
        env = build_app_envelope(
            from_node=HOST_NODE_ID,
            to_node=to_node,
            namespace=namespace,
            type=type,
            body=body,
            request_id=request_id,
        )
        try:
            await self._route_send(env)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            # Preserve aborted-request subtypes caught through TimeoutError.
            if isinstance(exc, RequestTimeoutError):
                raise
            _log.warning(
                "host APP request timed out",
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
            self._pending_request_targets.pop(request_id, None)

    async def _accept_loop(self) -> None:
        assert self._server is not None
        try:
            async for conn in self._server.connections():
                asyncio.create_task(self._handle_new_connection(conn))
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception("accept loop crashed")

    async def _handle_new_connection(self, conn: Connection) -> None:
        try:
            _log.debug("new connection accepted; starting handshake")
            client = await self._perform_handshake(conn)
            if client is None:
                return
            self._clients[client.client_id] = client
            is_new = self._membership.join(
                client.client_id,
                client.capabilities,
                now=time.monotonic(),
            )
            client.read_task = asyncio.create_task(self._client_read_loop(client))
            client.write_task = asyncio.create_task(self._client_write_loop(client))
            _log.info(
                "client registered",
                client_name=client.client_id,
                capabilities=list(client.capabilities),
                first_join=is_new,
                connected_count=len(self._clients),
            )
        except Exception:
            _log.exception("new connection handler failed")
            try:
                await conn.close()
            except Exception:
                pass

    async def _perform_handshake(self, conn: Connection) -> ConnectedClient | None:
        """Receive Hello, validate, send Welcome or Reject."""
        try:
            raw = await conn.recv_frame()
        except ConnectionClosed:
            return None
        try:
            env = Envelope.decode(raw)
        except EnvelopeDecodeError as exc:
            _log.warning("malformed first envelope: %s", exc)
            await conn.close()
            return None

        if env.kind is not EnvelopeKind.HELLO:
            _log.warning("first envelope is %s, not HELLO; closing", env.kind.value)
            await conn.close()
            return None

        try:
            hello = parse_hello(env)
        except ProtocolError as exc:
            _log.warning("invalid Hello: %s", exc)
            await conn.close()
            return None

        _log.debug(
            "received HELLO",
            client_name=hello.client_name,
            protocol_version=hello.protocol_version,
            framework_version=hello.framework_version,
            capabilities=list(hello.capabilities),
        )

        if not protocol_compatible(hello.protocol_version):
            _log.warning(
                "rejecting client: protocol mismatch",
                client_name=hello.client_name,
                client_protocol=hello.protocol_version,
                host_protocol=LAB_PROTOCOL_VERSION,
            )
            await self._send_reject(
                conn,
                hello.client_name,
                reason="protocol_mismatch",
                detail=(
                    f"client speaks {hello.protocol_version}; "
                    f"host supports {LAB_PROTOCOL_VERSION}"
                ),
            )
            return None

        if not self._auth.validate_hello(hello):
            _log.warning("rejecting client: auth failed", client_name=hello.client_name)
            await self._send_reject(
                conn,
                hello.client_name,
                reason="auth_failed",
                detail="token validation failed",
            )
            return None

        client_id = hello.client_name
        if client_id in self._blocked_clients:
            _log.warning("rejecting client: blocked", client_name=client_id)
            await self._send_reject(
                conn,
                client_id,
                reason="blocked",
                detail=f"client name {client_id!r} is blocked by operator policy",
            )
            return None
        if client_id in self._clients:
            _log.warning("rejecting client: name conflict", client_name=client_id)
            await self._send_reject(
                conn,
                client_id,
                reason="name_conflict",
                detail=f"client name {client_id!r} already connected",
            )
            return None

        welcome = WelcomePayload(
            protocol_version=LAB_PROTOCOL_VERSION,
            framework_version=self._framework_version,
            host_node_id=HOST_NODE_ID,
            assigned_client_id=client_id,
            supported_verbs=("send", "broadcast"),
            cluster_info={
                "nodes": sorted(self._membership.alive() | {HOST_NODE_ID}),
            },
        )
        try:
            await conn.send_frame(build_welcome(welcome, to_node=client_id).encode())
        except ConnectionClosed:
            _log.warning("welcome send failed: closed", client_name=client_id)
            return None

        _log.debug("WELCOME sent", client_name=client_id)
        return ConnectedClient(
            client_id=client_id,
            connection=conn,
            capabilities=hello.capabilities,
            send_buffer=BoundedSendBuffer(
                maxsize=self._config.backpressure_buffer_size
            ),
        )

    async def _send_reject(
        self,
        conn: Connection,
        to_node: str,
        *,
        reason: str,
        detail: str,
    ) -> None:
        reject = build_reject(
            RejectPayload(reason=reason, detail=detail),
            to_node=to_node,
        )
        try:
            await conn.send_frame(reject.encode())
        except ConnectionClosed:
            pass
        await conn.close()

    async def _client_read_loop(self, client: ConnectedClient) -> None:
        try:
            while not client.is_disconnecting:
                try:
                    raw = await client.connection.recv_frame()
                except ConnectionClosed:
                    break
                try:
                    env = Envelope.decode(raw)
                except EnvelopeDecodeError as exc:
                    _log.warning(
                        "client %s sent malformed envelope: %s",
                        client.client_id,
                        exc,
                    )
                    continue
                await self._route_envelope(client, env)
        except Exception:
            _log.exception(
                "host read loop crashed for client %s",
                client.client_id,
            )
        finally:
            await self._disconnect_client(client, reason="read loop ended")

    async def _client_write_loop(self, client: ConnectedClient) -> None:
        try:
            while not client.is_disconnecting:
                env = await client.send_buffer.get()
                try:
                    await client.connection.send_frame(env.encode())
                except ConnectionClosed:
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception(
                "host write loop crashed for client %s",
                client.client_id,
            )

    async def _disconnect_client(
        self,
        client: ConnectedClient,
        *,
        reason: str,
    ) -> None:
        if client.is_disconnecting:
            return
        client.is_disconnecting = True
        # Evict registries first so immediate reconnects can reuse the client ID.
        self._addressing.evict_node(client.client_id)
        self._membership.leave(client.client_id)
        self._clients.pop(client.client_id, None)
        # Notify consumers before failing requests; callback faults must not stop cleanup.
        for cb in list(self._disconnect_callbacks):
            try:
                cb(client.client_id)
            except Exception:
                _log.exception(
                    "disconnect callback raised for client %s",
                    client.client_id,
                )
        # Iterate a snapshot because each resumed request removes its own entries.
        aborted = 0
        for request_id, target in list(self._pending_request_targets.items()):
            if target != client.client_id:
                continue
            future = self._pending_requests.get(request_id)
            if future is not None and not future.done():
                future.set_exception(
                    RequestAbortedError(
                        f"client {client.client_id!r} disconnected before "
                        f"responding to request {request_id}"
                    )
                )
                aborted += 1
        _log.info(
            "client disconnected",
            client_name=client.client_id,
            reason=reason,
            aborted_requests=aborted,
            remaining_clients=len(self._clients),
        )
        if client.write_task is not None:
            client.write_task.cancel()
        try:
            await client.connection.close()
        except Exception:
            pass
        if client.write_task is not None:
            try:
                await client.write_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _route_envelope(
        self,
        sender: ConnectedClient,
        env: Envelope,
    ) -> None:
        if env.from_node != sender.client_id:
            _log.warning(
                "Dropping envelope with forged source identity",
                authenticated_peer=sender.client_id,
                claimed_source=env.from_node,
                target=env.to_node,
            )
            return
        match env.kind:
            case EnvelopeKind.HEARTBEAT:
                self._membership.heartbeat(sender.client_id, now=time.monotonic())
            case EnvelopeKind.HELLO | EnvelopeKind.WELCOME:
                _log.warning(
                    "client %s sent unexpected %s after handshake",
                    sender.client_id,
                    env.kind.value,
                )
            case EnvelopeKind.CONTROL:
                # Non-host CONTROL envelopes remain routable like SEND envelopes.
                if env.to_node == HOST_NODE_ID:
                    await self._handle_control(sender, env)
                else:
                    await self._route_send_forwarded(env)
            case EnvelopeKind.APP:
                if env.to_node == HOST_NODE_ID:
                    await self._handle_app(sender, env)
                elif is_host_only_client_forward(env):
                    _log.warning(
                        "dropping client-to-client control-plane APP to %s",
                        env.to_node,
                    )
                else:
                    await self._route_send_forwarded(env)
            case EnvelopeKind.BROADCAST:
                await self._fanout_broadcast(env)
            case EnvelopeKind.SEND | EnvelopeKind.ACK:
                await self._route_send_forwarded(env)
            case _:
                # Newer peers may send envelope kinds this host does not support.
                _log.warning(
                    "client %s sent envelope of unsupported kind %s",
                    sender.client_id,
                    env.kind.value,
                )

    async def _route_send_forwarded(self, env: Envelope) -> None:
        """Route a forwarded envelope without disconnecting its sender on backpressure."""
        try:
            await self._route_send(env)
        except BackpressureError:
            pass

    async def _route_send(self, env: Envelope) -> None:
        # Resolve creature refs first, then load-balanced channels, then direct IDs.
        target_node: str | None = self._addressing.resolve_creature(env.to_node)
        if target_node is None and env.to_node.startswith("channel://"):
            channel = env.to_node[len("channel://") :]
            target_node = self._addressing.pick_listener(channel)
        if target_node is None:
            target_node = env.to_node
        if target_node == HOST_NODE_ID:
            # The host does not own a local SEND endpoint.
            return
        client = self._clients.get(target_node)
        if client is None:
            _log.debug(
                "dropping SEND to unknown node %s (seq=%d)",
                env.to_node,
                env.seq,
            )
            return
        await self._enqueue(client, env)

    async def _fanout_broadcast(self, env: Envelope) -> None:
        channel = env.to_node
        if channel.startswith("topic://"):
            channel = channel[len("topic://") :]
        # Broadcast is fire-and-forget, so one full outbox cannot block other listeners.
        for node_id in self._addressing.listeners(channel):
            client = self._clients.get(node_id)
            if client is None:
                continue
            try:
                await self._enqueue(client, env)
            except BackpressureError:
                pass

    async def _handle_control(
        self,
        sender: ConnectedClient,
        env: Envelope,
    ) -> None:
        try:
            control_type, fields = parse_control(env)
        except ControlError as exc:
            _log.warning(
                "client %s sent malformed CONTROL: %s",
                sender.client_id,
                exc,
            )
            return
        match control_type:
            case "subscribe":
                channel = fields.get("channel")
                if isinstance(channel, str):
                    self._addressing.register_listener(channel, sender.client_id)
            case "unsubscribe":
                channel = fields.get("channel")
                if isinstance(channel, str):
                    self._addressing.unregister_listener(channel, sender.client_id)
            case "register_creature":
                ref = fields.get("ref")
                if isinstance(ref, str):
                    self._addressing.register_creature(ref, sender.client_id)
            case "unregister_creature":
                ref = fields.get("ref")
                if isinstance(ref, str):
                    self._addressing.unregister_creature(ref)
            case _:
                handler = self._control_handlers.get(control_type)
                if handler is None:
                    _log.debug(
                        "client %s sent CONTROL %r (no handler registered)",
                        sender.client_id,
                        control_type,
                    )
                    return
                try:
                    await handler(sender, env, fields)
                except Exception:
                    _log.exception(
                        "registered CONTROL handler for %r raised",
                        control_type,
                    )

    async def _handle_app(
        self,
        sender: ConnectedClient,
        env: Envelope,
    ) -> None:
        try:
            msg = parse_app_envelope(env)
        except AppMessageError as exc:
            _log.warning(
                "client %s sent malformed APP: %s",
                sender.client_id,
                exc,
            )
            return
        # Responses complete pending host requests rather than invoking extensions.
        if msg.in_reply_to is not None:
            future = self._pending_requests.pop(msg.in_reply_to, None)
            self._pending_request_targets.pop(msg.in_reply_to, None)
            if future is not None and not future.done():
                _log.debug(
                    "APP response received",
                    from_node=sender.client_id,
                    namespace=msg.namespace,
                    msg_type=msg.type,
                    in_reply_to=msg.in_reply_to,
                )
                future.set_result(msg.body)
            else:
                _log.debug(
                    "received APP response with no pending request " "(in_reply_to=%s)",
                    msg.in_reply_to,
                )
            return
        _log.debug(
            "APP request received",
            from_node=sender.client_id,
            namespace=msg.namespace,
            msg_type=msg.type,
            request_id=msg.request_id,
        )
        handler = self._app_extensions.get(msg.namespace)
        if handler is None:
            _log.debug(
                "client %s sent APP for namespace %r (no extension registered)",
                sender.client_id,
                msg.namespace,
            )
            return
        # Run handlers separately so nested requests can receive responses on this read loop.
        asyncio.create_task(
            self._run_handler_and_reply(msg, handler),
            name=f"app_handler_{msg.namespace}_{msg.type}",
        )

    async def _run_handler_and_reply(
        self,
        msg: Any,
        handler: Any,
    ) -> None:
        """Run an APP extension and return its response when requested."""
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
        response = build_app_envelope(
            from_node=HOST_NODE_ID,
            to_node=msg.sender_node,
            namespace=msg.namespace,
            type=msg.type,
            body=result,
            in_reply_to=msg.request_id,
        )
        _log.debug(
            "APP response sent",
            to_node=msg.sender_node,
            namespace=msg.namespace,
            msg_type=msg.type,
            in_reply_to=msg.request_id,
        )
        # A full sender outbox must not fail the background handler task.
        await self._route_send_forwarded(response)

    async def _enqueue(self, client: ConnectedClient, env: Envelope) -> None:
        """Enqueue without waiting, raising when the client outbox is full."""
        try:
            await client.send_buffer.put(env, wait=False)
        except BackpressureError:
            _log.error(
                "backpressure: dropping envelope to client %s " "(overflow_count=%d)",
                client.client_id,
                client.send_buffer.overflow_count,
            )
            raise

    async def _reaper_loop(self) -> None:
        try:
            while not self._stopped:
                await asyncio.sleep(self._reaper_interval_seconds)
                lost = self._membership.check_lost(now=time.monotonic())
                for node_id in lost:
                    _log.warning(
                        "membership lost (heartbeat timeout)",
                        client_name=node_id,
                    )
                    client = self._clients.get(node_id)
                    if client is not None:
                        await self._disconnect_client(
                            client, reason="heartbeat timeout"
                        )
        except asyncio.CancelledError:
            pass


_BUILTIN_CONTROL_TYPES = frozenset(
    {"subscribe", "unsubscribe", "register_creature", "unregister_creature"}
)


__all__ = ["ConnectedClient", "ControlHandler", "HostEngine"]
