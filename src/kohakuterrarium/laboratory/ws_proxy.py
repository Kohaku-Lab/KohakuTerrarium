"""Unified WebSocket forwarder for Lab adapters.

Many lab-host features need to surface a worker-local WebSocket as if
it were running on the controller: chat IO, PTY shells, file watch,
future log tail and trace streams.  Each one shares the same shape:

- The controller's HTTP/WS layer accepts a real WebSocket.
- A lab stream carries frames bidirectionally to a worker adapter.
- The worker adapter runs a producer task that emits frames (chat
  output, PTY stdout, file-change events) and a consumer that
  receives frames (user input, PTY stdin, resize).
- Lifecycle: ``start`` opens the session, ``input`` ferries frames
  from the controller back to the worker, ``cancel`` closes both
  sides.

Implementing this twice (once per WS) means duplicating ~300 lines of
queue / pump / cleanup / backpressure / cancellation logic.  This
module factors it out:

- :class:`WSFrameSink` — bidirectional bridge a worker-side producer
  uses as if it were a WebSocket.  ``await sink.send_json(frame)``
  pumps onto the lab stream; ``await sink.receive_json()`` pulls
  from frames the controller forwarded via ``input`` RPC.
- :class:`WSProxyAdapter` — base class for the worker-side APP
  extension.  Handles ``start`` / ``input`` / ``cancel`` dispatch and
  per-stream sink lifecycle.  Subclasses implement
  :meth:`on_start` (spawn the producer / consumer tasks bound to the
  sink) and :meth:`on_close` (teardown subprocess / observers).
- :func:`proxy_ws_to_lab` — controller-side helper.  Opens a
  :class:`RemoteStream`, forwards frames to the WS verbatim, and
  forwards inbound WS frames as ``input`` RPCs to the worker.

Backpressure: each direction uses a bounded queue.  Outbox (worker →
controller) blocks the producer when the controller's WS is slow to
drain — preferable to OOM under burst loads (PTY ``ls /``, etc.).
Inbox (controller → worker) blocks the input RPC when the worker's
consumer hasn't caught up; the controller surfaces this as a
request timeout the WS layer can ignore (a slow worker is the
worker's problem).
"""

import asyncio
from typing import Any

from fastapi import WebSocket

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import (
    LabNotifier,
    LabRegistrar,
    LabSender,
)
from kohakuterrarium.laboratory.streams import RemoteStream, StreamDemux
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Outbox cap — worker producers blocked beyond this back off until the
# controller drains.  PTY's ``ls /tmp`` can dump ~10K small frames in
# a burst; 4K is comfortable for chat and tight enough that a stuck
# controller is detectable.
_DEFAULT_OUTBOX_CAP = 4096
# Inbox is rarely full because input frames are user-generated; small
# cap is fine and surfaces backpressure quickly.
_DEFAULT_INBOX_CAP = 256


class WSFrameSink:
    """Bidirectional bridge that looks like a WebSocket to a producer.

    Worker-side adapters' producer tasks call :meth:`send_json` (push
    onto the outbox; pump drains to ``terrarium.stream`` notify);
    consumer tasks call :meth:`receive_json` (pull from the inbox the
    controller populated via ``input`` RPC).

    Lifecycle:

    1. Construct.
    2. :meth:`start` — spawns the pump task.
    3. Producer uses :meth:`send_json` (async, may block on
       backpressure).
    4. Consumer uses :meth:`receive_json` (async).
    5. :meth:`close` — flushes a sentinel ``{"eof": True}`` frame for
       the controller's iterator to terminate cleanly, then stops.

    Both directions are bounded — backpressure surfaces as a slow
    ``put`` rather than OOM.  ``inject_input`` is the worker-side
    adapter's hook for stuffing frames from an inbound RPC.
    """

    def __init__(
        self,
        node: LabNotifier,
        consumer: str,
        stream_id: str,
        *,
        outbox_cap: int = _DEFAULT_OUTBOX_CAP,
        inbox_cap: int = _DEFAULT_INBOX_CAP,
    ) -> None:
        self._node = node
        self._consumer = consumer
        self._stream_id = stream_id
        self._outbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue(outbox_cap)
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue(inbox_cap)
        self._pump: asyncio.Task | None = None
        self._closed = False

    @property
    def stream_id(self) -> str:
        return self._stream_id

    def start(self) -> None:
        if self._pump is None:
            self._pump = asyncio.create_task(self._drain_outbox())

    async def close(self) -> None:
        """Send EOF + stop the pump.  Idempotent."""
        if self._closed:
            return
        self._closed = True
        # Best-effort eof — if outbox is wedged, give up after a beat.
        try:
            await asyncio.wait_for(self._outbox.put({"eof": True}), timeout=1.0)
        except (asyncio.TimeoutError, asyncio.QueueFull):
            pass
        if self._pump is not None:
            # Drain whatever made it in before cancelling so trailing
            # frames don't disappear into a cancelled task.  Then cancel.
            for _ in range(10):
                if self._outbox.empty():
                    break
                await asyncio.sleep(0.01)
            self._pump.cancel()
            self._pump = None

    # ─── Producer (worker-side) ────────────────────────────────────

    async def send_json(self, frame: dict[str, Any]) -> None:
        if self._closed:
            return
        await self._outbox.put(frame)

    def send_json_nowait(self, frame: dict[str, Any]) -> None:
        """Non-blocking send.  Drops if outbox is full + logs at DEBUG.

        Use this from sync callbacks (e.g. channel ``on_send`` hooks)
        that can't ``await``.  Buffer overflow drops the frame —
        preferable to a noisy exception inside a sync hook.
        """
        if self._closed:
            return
        try:
            self._outbox.put_nowait(frame)
        except asyncio.QueueFull:
            logger.debug(
                "ws-proxy outbox full; dropping frame",
                stream_id=self._stream_id,
                consumer=self._consumer,
                frame_type=frame.get("type"),
            )

    # ─── Consumer (worker-side) ────────────────────────────────────

    async def receive_json(self) -> dict[str, Any]:
        return await self._inbox.get()

    # ─── Adapter hooks (worker-side) ───────────────────────────────

    async def inject_input(self, frame: dict[str, Any]) -> None:
        """Adapter's ``input`` RPC body lands here.  Surfaces backpressure
        as a slow RPC — the controller's request will time out and the
        WS layer logs / drops."""
        await self._inbox.put(frame)

    # ─── Internal ──────────────────────────────────────────────────

    async def _drain_outbox(self) -> None:
        try:
            while True:
                frame = await self._outbox.get()
                payload = dict(frame)
                payload["stream_id"] = self._stream_id
                try:
                    await self._node.notify(
                        to_node=self._consumer,
                        namespace=StreamDemux.NAMESPACE,
                        type="frame",
                        body=payload,
                    )
                except Exception:
                    logger.debug(
                        "ws-proxy frame delivery failed",
                        consumer=self._consumer,
                        stream_id=self._stream_id,
                    )
                    # Drop the frame and keep pumping — a transient
                    # transport blip shouldn't tear down the producer.
        except asyncio.CancelledError:
            raise


class WSProxyAdapter:
    """Base class for worker-side WS-proxy APP extensions.

    Subclasses set :attr:`NAMESPACE` and implement :meth:`on_start`
    (spawn producer + consumer tasks bound to the sink) and
    :meth:`on_close` (teardown subprocess / observers).  The base
    class handles the lab APP dispatch table:

    - ``start({stream_id, ...subclass args})`` — opens a session.
      :meth:`on_start` returns optional setup data (e.g. an initial
      ``setup`` frame the controller forwards before the first
      streamed frame).
    - ``input({stream_id, frame})`` — push frame into the sink's
      inbox; the subclass's consumer task pulls it.  Returns
      ``{"accepted": True}``.
    - ``cancel({stream_id})`` — calls :meth:`on_close` then closes
      the sink.

    Each open stream gets its own :class:`WSFrameSink` keyed by the
    controller-generated ``stream_id``.  Concurrent streams (e.g. one
    chat WS + one PTY WS for the same creature) are independent.
    """

    NAMESPACE: str = ""

    def __init__(self, lab_node: LabRegistrar) -> None:
        if not self.NAMESPACE:
            raise ValueError(f"{type(self).__name__} must set NAMESPACE")
        self._node = lab_node
        self._sinks: dict[str, WSFrameSink] = {}
        self._sessions: dict[str, Any] = {}
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        logger.info("lab adapter registered", namespace=self.NAMESPACE)

    def detach(self) -> None:
        """Synchronously unregister the extension and schedule teardown.

        Kept for sync shutdown paths (e.g. tests outside an event loop,
        legacy callers).  Inside a running event loop, prefer
        :meth:`adetach` — it awaits every per-stream teardown before
        returning so the post-detach contract ("no producer task still
        running") actually holds.

        Older revisions scheduled fire-and-forget tasks via
        ``create_task`` and returned immediately, which raced any
        caller that treated ``detach()`` as fully torn down.
        """
        stream_ids = list(self._sinks.keys())
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop is None or not loop.is_running():
            # No running loop — drive everything to completion so the
            # post-detach contract holds.
            async def _run_all() -> None:
                await asyncio.gather(
                    *(self._teardown(sid) for sid in stream_ids),
                    return_exceptions=True,
                )

            if loop is None or loop.is_closed():
                asyncio.run(_run_all())
            else:
                loop.run_until_complete(_run_all())
            self._node.unregister_app_extension(self.NAMESPACE)
            logger.info("lab adapter detached", namespace=self.NAMESPACE)
            return
        # Running loop — we can't block here.  Unregister synchronously
        # so no new dispatches arrive, and schedule the teardowns.
        # Callers needing the strong "all torn down" guarantee must use
        # :meth:`adetach` from async context.
        self._node.unregister_app_extension(self.NAMESPACE)
        self._pending_teardowns = [
            loop.create_task(self._teardown(sid)) for sid in stream_ids
        ]
        logger.info(
            "lab adapter detached (teardowns scheduled)",
            namespace=self.NAMESPACE,
            pending=len(self._pending_teardowns),
        )

    async def adetach(self) -> None:
        """Async detach — awaits every teardown before returning.

        The post-condition is the strong one: when this coroutine
        resolves, every per-stream :meth:`_teardown` (and therefore
        every :meth:`on_close` hook) has run to completion and the APP
        extension is unregistered.
        """
        stream_ids = list(self._sinks.keys())
        self._node.unregister_app_extension(self.NAMESPACE)
        await asyncio.gather(
            *(self._teardown(sid) for sid in stream_ids),
            return_exceptions=True,
        )
        logger.info("lab adapter detached", namespace=self.NAMESPACE)

    async def _dispatch(self, msg: AppMessage) -> dict[str, Any]:
        try:
            return await self._handle(msg)
        except KeyError as e:
            return {"error": {"kind": "not_found", "message": str(e)}}
        except ValueError as e:
            return {"error": {"kind": "invalid", "message": str(e)}}
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("%s handler failed: %s", self.NAMESPACE, msg.type)
            return {"error": {"kind": "proxy", "message": str(e)}}

    async def _handle(self, msg: AppMessage) -> dict[str, Any]:
        match msg.type:
            case "start":
                return await self._op_start(msg)
            case "input":
                return await self._op_input(msg)
            case "cancel":
                return await self._op_cancel(msg)
            case _:
                return {
                    "error": {
                        "kind": "unknown_type",
                        "message": f"unsupported {self.NAMESPACE} type: {msg.type!r}",
                    }
                }

    async def _op_start(self, msg: AppMessage) -> dict[str, Any]:
        stream_id = msg.body["stream_id"]
        consumer = msg.sender_node
        sink = WSFrameSink(self._node, consumer, stream_id)
        sink.start()
        self._sinks[stream_id] = sink
        try:
            extras = await self.on_start(msg.body, sink) or {}
        except Exception:
            await self._teardown(stream_id)
            raise
        return {"started": True, "stream_id": stream_id, **extras}

    async def _op_input(self, msg: AppMessage) -> dict[str, Any]:
        stream_id = msg.body["stream_id"]
        sink = self._sinks.get(stream_id)
        if sink is None:
            raise KeyError(f"stream {stream_id!r} not active")
        await sink.inject_input(msg.body["frame"])
        return {"accepted": True}

    async def _op_cancel(self, msg: AppMessage) -> dict[str, Any]:
        stream_id = msg.body["stream_id"]
        await self._teardown(stream_id)
        return {"cancelled": True, "stream_id": stream_id}

    async def _teardown(self, stream_id: str) -> None:
        sink = self._sinks.pop(stream_id, None)
        self._sessions.pop(stream_id, None)
        try:
            await self.on_close(stream_id)
        except Exception:
            logger.exception("%s on_close failed", self.NAMESPACE)
        if sink is not None:
            await sink.close()

    # ─── Subclass hooks ────────────────────────────────────────────

    async def on_start(
        self,
        body: dict[str, Any],
        sink: WSFrameSink,
    ) -> dict[str, Any] | None:
        """Subclass spawns producer / consumer tasks bound to ``sink``.

        Return an optional dict merged into the ``start`` response.
        Common keys: ``setup`` (a dict the controller will forward as
        the FIRST frame to the WS — used for session_info / banner).
        """
        raise NotImplementedError

    async def on_close(self, stream_id: str) -> None:
        """Subclass teardown — kill subprocess, remove observers, etc.

        Called BEFORE the sink is closed so producers see a clean
        cancellation before EOF flushes through.
        """
        return None


# ---------------------------------------------------------------------------
# Controller-side helper.
# ---------------------------------------------------------------------------


async def proxy_ws_to_lab(
    *,
    websocket: WebSocket,
    sender: LabSender,
    demux: StreamDemux,
    target_node: str,
    namespace: str,
    body: dict[str, Any],
    timeout: float = 60.0,
    input_timeout: float = 10.0,
) -> None:
    """Open a lab proxy stream against a worker and bridge to ``websocket``.

    The worker MUST register a subclass of :class:`WSProxyAdapter` on
    ``namespace``.  This helper:

    1. Opens a :class:`RemoteStream` against ``{namespace}.start``.
    2. Forwards every stream frame to ``websocket.send_json`` verbatim
       (stripping the demux ``stream_id`` wrapper).
    3. If the start response carries a ``setup`` dict, forwards that
       first — before any streamed frame.
    4. Forwards every WS frame to ``{namespace}.input`` RPC.
    5. On WS disconnect or stream EOF, cancels the forward task and
       calls ``aclose()`` (which sends ``{namespace}.cancel`` upstream).
    """
    rs = await RemoteStream.open(
        demux=demux,
        sender=sender,
        target_node=target_node,
        start_namespace=namespace,
        start_type="start",
        cancel_namespace=namespace,
        body=body,
        timeout=timeout,
    )

    setup = (rs.start_response or {}).get("setup")
    if isinstance(setup, dict):
        await websocket.send_json(setup)

    async def _forward_stream_to_ws() -> None:
        try:
            async for frame in rs:
                if "eof" in frame:
                    break
                # Strip the demux routing wrapper.
                ws_frame = {k: v for k, v in frame.items() if k != "stream_id"}
                await websocket.send_json(ws_frame)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("ws-proxy forward ended", error=str(exc))

    fwd_task = asyncio.create_task(_forward_stream_to_ws())
    stream_id = rs.stream_id

    try:
        while True:
            data = await websocket.receive_json()
            try:
                await sender.request(
                    to_node=target_node,
                    namespace=namespace,
                    type="input",
                    body={"stream_id": stream_id, "frame": data},
                    timeout=input_timeout,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("ws-proxy input forward failed", error=str(exc))
    finally:
        fwd_task.cancel()
        await rs.aclose()


__all__ = [
    "WSFrameSink",
    "WSProxyAdapter",
    "proxy_ws_to_lab",
]
