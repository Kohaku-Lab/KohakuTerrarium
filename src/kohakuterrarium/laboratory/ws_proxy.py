"""Unified WebSocket forwarder for Lab adapters.

Bridge controller WebSockets to worker-local producers and consumers over lab
streams. Worker-to-controller and controller-to-worker queues are bounded so
slow peers apply backpressure rather than allowing unbounded memory growth.
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


# The outbox accommodates bursty PTY output while still making a stalled
# controller apply backpressure.
_DEFAULT_OUTBOX_CAP = 4096
# User-generated input is low-volume, so a small inbox exposes slow consumers.
_DEFAULT_INBOX_CAP = 256


class WSFrameSink:
    """Provide a bounded bidirectional frame bridge for worker adapters.

    Producers send through the outbox to lab notifications; consumers receive
    controller input from the inbox. Closing emits an EOF frame before stopping.
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
        """Best-effort send EOF and stop the pump; safe to call repeatedly."""
        if self._closed:
            return
        self._closed = True
        # A wedged outbox must not prevent shutdown indefinitely.
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

    async def receive_json(self) -> dict[str, Any]:
        return await self._inbox.get()

    async def inject_input(self, frame: dict[str, Any]) -> None:
        """Queue an input RPC frame, applying backpressure to the request."""
        await self._inbox.put(frame)

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
                    # A transient delivery failure must not stop the producer.
        except asyncio.CancelledError:
            raise


class WSProxyAdapter:
    """Manage worker-side WebSocket proxy sessions and APP dispatch.

    Subclasses provide a namespace and session startup and teardown hooks. Each
    controller-generated stream ID owns an independent frame sink.
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
        """Unregister synchronously and tear down or schedule active sessions.

        Without a running loop, teardown completes before return. Within a
        running loop, callers needing that guarantee must await :meth:`adetach`.
        """
        stream_ids = list(self._sinks.keys())
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop is None or not loop.is_running():
            # Without a running loop, teardown can complete synchronously.
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
        # Unregister before scheduling teardown so no new sessions can arrive.
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
        """Unregister and await teardown of every active session."""
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

    async def on_start(
        self,
        body: dict[str, Any],
        sink: WSFrameSink,
    ) -> dict[str, Any] | None:
        """Start tasks bound to ``sink`` and return optional setup data."""
        raise NotImplementedError

    async def on_close(self, stream_id: str) -> None:
        """Tear down a session before its sink emits EOF and closes."""
        return None


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
    """Bridge a controller WebSocket to a worker proxy stream.

    Setup data is sent before stream frames. Disconnect or EOF closes the remote
    stream and sends its cancellation request upstream.
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
                # The stream ID is transport metadata, not a WebSocket frame field.
                ws_frame = {k: v for k, v in frame.items() if k != "stream_id"}
                await websocket.send_json(ws_frame)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("ws-proxy forward ended", error=str(exc), exc_info=True)

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
                logger.warning(
                    "ws-proxy input forward failed", error=str(exc), exc_info=True
                )
    finally:
        fwd_task.cancel()
        await rs.aclose()


__all__ = [
    "WSFrameSink",
    "WSProxyAdapter",
    "proxy_ws_to_lab",
]
