"""L3 stream state — sequence assignment, FIFO delivery, dedupe, retry.

Two classes:

- :class:`StreamSender` — assigns per-``stream_id`` sequence numbers.
  Remembers ack-required envelopes for retransmission.
- :class:`StreamReceiver` — delivers envelopes in FIFO order. Buffers
  out-of-order arrivals; drops duplicates (``seq < expected_seq``).

Per-stream isolation: callers maintain a sender/receiver pair per
logical stream. Time is provided by callers (``now`` parameter) so
tests can drive retry timing deterministically.
"""

from dataclasses import dataclass, field

from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeKind,
)

# Default ack timeout for ack-required sends, in seconds. The host/client
# engine polls due_for_retry() with this value. Tunable per-call via flags
# in a future revision; fixed default suffices for 1.5.0.
DEFAULT_ACK_TIMEOUT_SECONDS = 5.0

# Max envelopes a receiver will hold in its reorder buffer before dropping.
# Caps the per-stream memory footprint when sequences are pathologically
# delayed.
DEFAULT_REORDER_BUFFER_SIZE = 128


@dataclass
class StreamSender:
    """Per-``stream_id`` sender state.

    Maintains the next sequence number to assign and a buffer of
    in-flight ack-required envelopes for retransmission.
    """

    next_seq: int = 0
    pending: dict[int, tuple[Envelope, float]] = field(default_factory=dict)

    def assign_seq(self) -> int:
        """Return and consume the next monotonically increasing seq."""
        seq = self.next_seq
        self.next_seq += 1
        return seq

    def remember(self, env: Envelope, now: float) -> None:
        """Save an envelope for retransmit-on-timeout.

        Used for ack-required Send envelopes. The pair (envelope,
        send_time) is keyed by ``env.seq``.
        """
        self.pending[env.seq] = (env, now)

    def ack(self, seq: int) -> bool:
        """Drop a pending envelope after receiving its Ack.

        Returns ``True`` if a pending envelope was actually cleared,
        ``False`` if the seq was unknown (already acked, or never sent
        with ack_required).
        """
        return self.pending.pop(seq, None) is not None

    def due_for_retry(
        self,
        now: float,
        timeout: float = DEFAULT_ACK_TIMEOUT_SECONDS,
    ) -> list[Envelope]:
        """Return envelopes whose ack hasn't arrived within ``timeout``.

        Order is by seq ascending so callers can retransmit in the
        original order.
        """
        due = [
            (env, send_time)
            for env, send_time in self.pending.values()
            if now - send_time > timeout
        ]
        due.sort(key=lambda pair: pair[0].seq)
        return [env for env, _ in due]

    def mark_retried(self, seq: int, now: float) -> None:
        """Reset the send-time of a pending envelope after retransmit."""
        entry = self.pending.get(seq)
        if entry is not None:
            env, _ = entry
            self.pending[seq] = (env, now)

    def pending_count(self) -> int:
        """Number of envelopes awaiting ack."""
        return len(self.pending)


@dataclass
class StreamReceiver:
    """Per-``(from_node, stream_id)`` receiver state.

    Delivers envelopes in FIFO order on the (from_node, stream_id) key.
    Out-of-order arrivals are buffered until preceding sequences fill in.
    Duplicates (``seq < expected_seq``) are dropped silently.

    Reorder buffer is bounded; envelopes with ``seq`` far in the future
    are dropped once the buffer reaches ``max_reorder_buffer`` entries.
    Dropping in this case is safe at the application level because the
    sender's retransmit will arrive after the gap fills.
    """

    expected_seq: int = 0
    buffer: dict[int, Envelope] = field(default_factory=dict)
    max_reorder_buffer: int = DEFAULT_REORDER_BUFFER_SIZE

    def accept(self, env: Envelope) -> list[Envelope]:
        """Accept an incoming envelope; return envelopes ready for delivery.

        The returned list may have 0 or more envelopes in FIFO order:

        - 0 when ``env`` is a duplicate or buffered out-of-order;
        - 1 or more when ``env`` fills the next-expected slot and any
          buffered subsequent slots drain in sequence.
        """
        if env.seq < self.expected_seq:
            # Duplicate: already delivered or about to be (would-be replay).
            return []
        if env.seq == self.expected_seq:
            ready = [env]
            self.expected_seq += 1
            while self.expected_seq in self.buffer:
                ready.append(self.buffer.pop(self.expected_seq))
                self.expected_seq += 1
            return ready
        # env.seq > expected_seq: buffer for later.
        if len(self.buffer) >= self.max_reorder_buffer:
            # Buffer full — drop this far-future envelope. Sender's
            # retransmit will arrive after the gap fills.
            return []
        self.buffer[env.seq] = env
        return []

    def buffered_count(self) -> int:
        """Number of envelopes waiting in the reorder buffer."""
        return len(self.buffer)


def build_ack_envelope(
    *,
    from_node: str,
    to_node: str,
    stream_id: int,
    seq: int,
) -> Envelope:
    """Build an ACK envelope acknowledging ``(stream_id, seq)`` from ``to_node``.

    The ack's own ``stream_id`` and ``seq`` mirror the acked envelope —
    that's how the sender's :meth:`StreamSender.ack` locates the pending
    entry. ``from_node`` is the receiver (the side issuing the ack);
    ``to_node`` is the original sender.
    """
    return Envelope(
        from_node=from_node,
        to_node=to_node,
        kind=EnvelopeKind.ACK,
        stream_id=stream_id,
        seq=seq,
    )


__all__ = [
    "DEFAULT_ACK_TIMEOUT_SECONDS",
    "DEFAULT_REORDER_BUFFER_SIZE",
    "StreamReceiver",
    "StreamSender",
    "build_ack_envelope",
]
