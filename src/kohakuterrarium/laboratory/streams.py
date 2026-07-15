"""Lab APP-based bidirectional streams.

Multiplex long-running producer events over the ``terrarium.stream`` APP
namespace. Each consumer node installs one :class:`StreamDemux`, and each
:class:`RemoteStream` uses a unique stream ID for routing and cancellation.
"""

import asyncio
import uuid
from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabRegistrar, LabSender
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class RemoteStreamError(Exception):
    """Report a structured producer error during remote stream iteration."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class StreamDemux:
    """Route inbound frames to queues keyed by stream ID.

    Frames for unknown streams are dropped. When supported by the lab node,
    producer disconnects inject synthetic EOF frames so consumers unblock.
    """

    NAMESPACE = "terrarium.stream"

    def __init__(self, lab_node: LabRegistrar) -> None:
        self._node = lab_node
        self._queues: dict[str, asyncio.Queue] = {}
        # Producer ownership identifies streams to unblock when a node disconnects.
        self._stream_targets: dict[str, str] = {}
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        on_disc = getattr(lab_node, "on_node_disconnect", None)
        if callable(on_disc):
            try:
                on_disc(self._on_node_disconnect)
            except Exception:  # pragma: no cover - defensive
                logger.warning(
                    "stream demux: on_node_disconnect registration failed",
                    exc_info=True,
                )

    def register(
        self,
        stream_id: str,
        target_node: str | None = None,
    ) -> asyncio.Queue:
        if stream_id in self._queues:
            raise ValueError(f"stream_id {stream_id!r} already registered")
        q: asyncio.Queue = asyncio.Queue()
        self._queues[stream_id] = q
        if target_node is not None:
            self._stream_targets[stream_id] = target_node
        return q

    def unregister(self, stream_id: str) -> None:
        self._queues.pop(stream_id, None)
        self._stream_targets.pop(stream_id, None)

    def detach(self) -> None:
        """Unregister the APP extension. Idempotent."""
        self._node.unregister_app_extension(self.NAMESPACE)
        self._queues.clear()
        self._stream_targets.clear()

    async def _dispatch(self, msg: AppMessage) -> None:
        body = msg.body or {}
        stream_id = body.get("stream_id")
        if not isinstance(stream_id, str):
            return None
        q = self._queues.get(stream_id)
        if q is None:
            # Late frames are expected after cancellation and need no response.
            return None
        await q.put(body)
        return None  # Notifications intentionally have no response envelope.

    def _on_node_disconnect(self, node_id: str) -> None:
        """Unblock streams whose producer disconnected by injecting EOF frames."""
        for stream_id in list(self._stream_targets.keys()):
            if self._stream_targets.get(stream_id) != node_id:
                continue
            q = self._queues.get(stream_id)
            if q is None:
                continue
            try:
                q.put_nowait(
                    {"stream_id": stream_id, "eof": True, "disconnected": True}
                )
            except asyncio.QueueFull:  # pragma: no cover - defensive
                logger.warning(
                    "stream demux: queue full for stream %s on disconnect " "of %s",
                    stream_id,
                    node_id,
                    exc_info=True,
                )


class RemoteStream:
    """Iterate producer frames until EOF and propagate structured stream errors."""

    def __init__(
        self,
        demux: StreamDemux,
        stream_id: str,
        target_node: str,
        sender: LabSender,
        cancel_namespace: str,
    ) -> None:
        self._demux = demux
        self._stream_id = stream_id
        self._target_node = target_node
        self._sender = sender
        self._cancel_namespace = cancel_namespace
        self._queue = demux.register(stream_id, target_node=target_node)
        self._closed = False

    @classmethod
    async def open(
        cls,
        *,
        demux: StreamDemux,
        sender: LabSender,
        target_node: str,
        start_namespace: str,
        start_type: str,
        body: dict[str, Any],
        cancel_namespace: str | None = None,
        timeout: float = 5.0,
    ) -> "RemoteStream":
        """Start a producer stream using a generated routing identifier."""
        stream_id = uuid.uuid4().hex
        rs = cls(
            demux=demux,
            stream_id=stream_id,
            target_node=target_node,
            sender=sender,
            cancel_namespace=cancel_namespace or start_namespace,
        )
        try:
            response = await sender.request(
                to_node=target_node,
                namespace=start_namespace,
                type=start_type,
                body={**body, "stream_id": stream_id},
                timeout=timeout,
            )
        except BaseException:
            demux.unregister(stream_id)
            raise
        if isinstance(response, dict) and "error" in response:
            demux.unregister(stream_id)
            err = response["error"]
            raise RemoteStreamError(err.get("kind", "unknown"), err.get("message", ""))
        # Preserve setup metadata returned before asynchronous frames begin.
        rs._start_response = response if isinstance(response, dict) else {}
        return rs

    @property
    def stream_id(self) -> str:
        return self._stream_id

    @property
    def start_response(self) -> dict[str, Any]:
        return getattr(self, "_start_response", {})

    def __aiter__(self) -> "RemoteStream":
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._closed:
            raise StopAsyncIteration
        frame = await self._queue.get()
        if frame.get("eof"):
            await self._close_local()
            raise StopAsyncIteration
        if "error" in frame:
            err = frame["error"]
            await self._close_local()
            raise RemoteStreamError(err.get("kind", "unknown"), err.get("message", ""))
        return frame

    async def aclose(self) -> None:
        """Cancel the stream on the producer side and clean up locally."""
        if self._closed:
            return
        try:
            await self._sender.request(
                to_node=self._target_node,
                namespace=self._cancel_namespace,
                type="cancel_stream",
                body={"stream_id": self._stream_id},
                timeout=2.0,
            )
        except Exception:
            logger.debug(
                "best-effort cancel_stream failed for stream %s", self._stream_id
            )
        await self._close_local()

    async def __aenter__(self) -> "RemoteStream":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def _close_local(self) -> None:
        self._closed = True
        self._demux.unregister(self._stream_id)


__all__ = ["RemoteStream", "RemoteStreamError", "StreamDemux"]
