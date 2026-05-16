"""Unit tests for :mod:`kohakuterrarium.laboratory._internal.streams`."""

from kohakuterrarium.laboratory._internal.envelope import Envelope, EnvelopeKind
from kohakuterrarium.laboratory._internal.streams import (
    DEFAULT_ACK_TIMEOUT_SECONDS,
    DEFAULT_REORDER_BUFFER_SIZE,
    StreamReceiver,
    StreamSender,
    build_ack_envelope,
)


def _env(seq: int = 0, *, ack_required=False) -> Envelope:
    return Envelope(
        from_node="a",
        to_node="b",
        kind=EnvelopeKind.SEND,
        stream_id=1,
        seq=seq,
        payload=b"",
        flags={"ack_required": True} if ack_required else {},
    )


# ── StreamSender ────────────────────────────────────────────────


class TestStreamSender:
    def test_assign_seq_monotonic(self):
        s = StreamSender()
        assert s.assign_seq() == 0
        assert s.assign_seq() == 1
        assert s.assign_seq() == 2

    def test_remember_and_ack(self):
        s = StreamSender()
        env = _env(seq=0, ack_required=True)
        s.remember(env, now=10.0)
        assert s.pending_count() == 1
        assert s.ack(0) is True
        assert s.pending_count() == 0

    def test_ack_unknown_returns_false(self):
        s = StreamSender()
        assert s.ack(99) is False

    def test_due_for_retry(self):
        s = StreamSender()
        s.remember(_env(seq=0), now=0.0)
        s.remember(_env(seq=1), now=10.0)
        due = s.due_for_retry(now=20.0, timeout=5.0)
        seqs = [e.seq for e in due]
        # Both envelopes are due; ordered by seq.
        assert seqs == [0, 1]

    def test_due_for_retry_not_yet(self):
        s = StreamSender()
        s.remember(_env(seq=0), now=10.0)
        due = s.due_for_retry(now=11.0, timeout=5.0)
        assert due == []

    def test_mark_retried_resets_time(self):
        s = StreamSender()
        s.remember(_env(seq=0), now=0.0)
        s.mark_retried(0, now=100.0)
        # Now the send time is 100; nothing due yet at 101.
        assert s.due_for_retry(now=101.0, timeout=5.0) == []

    def test_mark_retried_unknown_noop(self):
        s = StreamSender()
        s.mark_retried(99, now=0.0)  # no raise


# ── StreamReceiver ──────────────────────────────────────────────


class TestStreamReceiver:
    def test_in_order_delivery(self):
        r = StreamReceiver()
        out = r.accept(_env(seq=0))
        assert len(out) == 1
        assert out[0].seq == 0
        out = r.accept(_env(seq=1))
        assert len(out) == 1

    def test_duplicate_dropped(self):
        r = StreamReceiver()
        r.accept(_env(seq=0))
        # Seq 0 again is a dup.
        assert r.accept(_env(seq=0)) == []

    def test_out_of_order_buffered(self):
        r = StreamReceiver()
        # Seq 1 arrives before seq 0.
        out = r.accept(_env(seq=1))
        assert out == []
        assert r.buffered_count() == 1
        # Now seq 0 lands → both should deliver.
        out = r.accept(_env(seq=0))
        seqs = [e.seq for e in out]
        assert seqs == [0, 1]
        assert r.buffered_count() == 0

    def test_buffer_overflow_drops(self):
        r = StreamReceiver(max_reorder_buffer=2)
        # Seq 1, 2 buffer (skipping 0).
        r.accept(_env(seq=1))
        r.accept(_env(seq=2))
        assert r.buffered_count() == 2
        # Seq 3 — buffer full → dropped.
        out = r.accept(_env(seq=3))
        assert out == []
        assert r.buffered_count() == 2


# ── build_ack_envelope ──────────────────────────────────────────


class TestBuildAckEnvelope:
    def test_basic(self):
        env = build_ack_envelope(
            from_node="recv",
            to_node="send",
            stream_id=7,
            seq=42,
        )
        assert env.kind == EnvelopeKind.ACK
        assert env.from_node == "recv"
        assert env.to_node == "send"
        assert env.stream_id == 7
        assert env.seq == 42


# ── module constants ────────────────────────────────────────────


class TestConstants:
    def test_default_ack_timeout(self):
        assert DEFAULT_ACK_TIMEOUT_SECONDS > 0

    def test_default_buffer_size(self):
        assert DEFAULT_REORDER_BUFFER_SIZE > 0
