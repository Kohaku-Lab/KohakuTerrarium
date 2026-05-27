"""Lab APP-based bidirectional streams.

Long-running producer-to-consumer event streams (chat tokens, engine
event subscriptions, file read/write chunks, ...) ride on a single
APP namespace ``terrarium.stream``.  Each stream gets a unique
``stream_id`` and demuxes through :class:`StreamDemux` installed on
the consumer node.

Streaming pattern:

::

    consumer (host)                              producer (client)
        │
        │  RemoteStream.open(
        │      demux, sender=host,
        │      target_node="worker-1",
        │      start_namespace="terrarium.events",
        │      start_type="start_chat",
        │      body={creature_id, message})
        │
        │  ── APP request: start_chat -----------▶│  spawn task,
        │      body={..., stream_id="abc"}        │  await chat tokens
        │  ◀── APP response: {started: true} ─────│
        │                                         │  for each token:
        │  ◀── APP notify: terrarium.stream/frame ┤    notify(host,
        │      body={stream_id: "abc",            │      "terrarium.stream",
        │            token: "Hello"}              │      "frame", {...})
        │                                         │
        │  ◀── APP notify: terrarium.stream/frame ┤  on natural end:
        │      body={stream_id: "abc",            │    one final frame
        │            eof: true}                   │    with eof=True
        │                                         │
        │  __anext__ raises StopAsyncIteration    │

Cancellation: consumer calls :meth:`RemoteStream.aclose` (or breaks out
of ``async for``), which sends a ``cancel_stream`` APP request to the
producer; the producer cancels its task and the next frame the demux
gets is dropped (the queue is unregistered).

Each lab node that consumes streams must install exactly one
:class:`StreamDemux`. Multiple concurrent streams over the same demux
are multiplexed by ``stream_id``.
"""

import asyncio
import uuid
from typing import Any

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.protocols import LabRegistrar, LabSender
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class RemoteStreamError(Exception):
    """Raised by :class:`RemoteStream` iteration when the producer reports an error.

    Attributes:
        kind: structured error tag from the producer side (e.g. ``"engine"``).
        message: human-readable detail.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


class StreamDemux:
    """Routes inbound stream frames to per-stream-id queues.

    Install once per lab node that consumes streams (typically the
    controller-side host).  Each :class:`RemoteStream` registers its
    own queue with this demux; frames arriving on namespace
    ``terrarium.stream`` are dispatched to the matching queue.

    A frame body always has key ``stream_id``.  Frames with no matching
    queue are dropped (the consumer cancelled the stream).

    Producer-disconnect surfacing: if the underlying lab node exposes
    an ``on_node_disconnect`` registration hook (HostEngine and
    ClientConnector both do), the demux registers a callback so that
    every stream whose producer ``target_node`` has gone away receives
    a synthetic ``{"eof": True, "disconnected": True}`` frame.  This
    unblocks :meth:`RemoteStream.__anext__` immediately on producer
    disconnect rather than hanging until the consumer's own RPC
    timeout fires.
    """

    NAMESPACE = "terrarium.stream"

    def __init__(self, lab_node: LabRegistrar) -> None:
        self._node = lab_node
        self._queues: dict[str, asyncio.Queue] = {}
        # Per-stream target node — populated by :class:`RemoteStream`
        # when it knows which producer it's reading from, so the demux
        # can drain streams attached to a node that just disconnected.
        self._stream_targets: dict[str, str] = {}
        lab_node.register_app_extension(self.NAMESPACE, self._dispatch)
        # If the lab node supports disconnect notifications, register
        # ourselves so we can drain streams when a producer goes away.
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
            # Stream cancelled or never registered — drop quietly.
            return None
        await q.put(body)
        return None  # No response — frames are fire-and-forget.

    def _on_node_disconnect(self, node_id: str) -> None:
        """Push an EOF frame into every stream whose producer just left.

        Called by the host/client engine when a peer node disconnects.
        Synchronous (the engine drives it inline during its own
        disconnect path); uses ``put_nowait`` so a slow consumer can't
        block the engine.  If the queue is full the consumer is
        already buried — we drop the EOF and let them notice via
        whatever wakes them up next.
        """
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
    """Async iterator over frames from a producer node.

    Iteration yields frame body dicts (with ``stream_id`` stripped from
    the payload's logical content but kept on the dict).  Terminates
    naturally on the producer's ``eof`` frame; raises
    :class:`RemoteStreamError` on an ``error`` frame.

    Use as an async context manager to guarantee cleanup:

    ::

        async with await RemoteStream.open(...) as rs:
            async for frame in rs:
                ...
    """

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
        """Start a stream and return its iterator handle.

        The ``start_type`` request body is augmented with
        ``stream_id``; the producer must echo / honor that id when
        emitting frames.
        """
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
        # Stash the start-RPC response so callers that need a setup-time
        # handshake payload (e.g. ``terrarium.attach.start_attach``
        # returning the initial ``session_info`` frame) can read it.
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
