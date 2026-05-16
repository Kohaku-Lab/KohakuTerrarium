"""Backpressure for outbound envelope buffers.

A :class:`BoundedSendBuffer` is a bounded async queue with overflow
diagnostics. Senders ``put`` envelopes; the transport drains via
``get``. When the queue is full:

- ``put(wait=True)`` (default) blocks until space is available.
- ``put(wait=False)`` raises :class:`BackpressureError` immediately.

A counter records overflow events for telemetry; the 1.5.0 host engine
logs these and (in the future) may pause local creature work when
sustained backpressure occurs.

The default capacity is 1000 envelopes.
"""

import asyncio

from kohakuterrarium.laboratory._internal.envelope import Envelope

DEFAULT_BUFFER_SIZE = 1000


class BackpressureError(Exception):
    """Raised by :meth:`BoundedSendBuffer.put` with ``wait=False`` when full."""


class BoundedSendBuffer:
    """A bounded async buffer for outbound envelopes."""

    def __init__(self, maxsize: int = DEFAULT_BUFFER_SIZE) -> None:
        if maxsize <= 0:
            raise ValueError(f"maxsize must be positive, got {maxsize}")
        self._queue: asyncio.Queue[Envelope] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._overflow_count = 0

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def overflow_count(self) -> int:
        """Number of times :meth:`put` was rejected for being full."""
        return self._overflow_count

    def qsize(self) -> int:
        """Current number of buffered envelopes."""
        return self._queue.qsize()

    def is_full(self) -> bool:
        return self._queue.full()

    async def put(self, env: Envelope, *, wait: bool = True) -> None:
        """Enqueue an envelope.

        Args:
            env: The envelope to buffer.
            wait: If ``True`` (default), block until space is available.
                If ``False``, raise :class:`BackpressureError` immediately
                when the buffer is full.
        """
        if wait:
            await self._queue.put(env)
            return
        try:
            self._queue.put_nowait(env)
        except asyncio.QueueFull as exc:
            self._overflow_count += 1
            raise BackpressureError(
                f"send buffer full ({self._maxsize} envelopes)"
            ) from exc

    async def get(self) -> Envelope:
        """Dequeue the next envelope. Blocks until one is available."""
        return await self._queue.get()

    def get_nowait(self) -> Envelope | None:
        """Dequeue immediately or return ``None`` if empty."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None


__all__ = [
    "BackpressureError",
    "BoundedSendBuffer",
    "DEFAULT_BUFFER_SIZE",
]
