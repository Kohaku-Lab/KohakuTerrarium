"""Unit tests for :mod:`kohakuterrarium.laboratory._internal.envelope`."""

import pytest
from kohakuvault import DataPacker

from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeDecodeError,
    EnvelopeKind,
)

_PACKER = DataPacker("msgpack")


# ── EnvelopeKind ──────────────────────────────────────────────────


class TestEnvelopeKind:
    def test_values(self):
        assert EnvelopeKind.SEND.value == "send"
        assert EnvelopeKind.BROADCAST.value == "broadcast"
        assert EnvelopeKind.APP.value == "app"

    def test_str_comparable(self):
        assert EnvelopeKind.SEND == "send"


# ── Envelope basic API ────────────────────────────────────────────


class TestEnvelopeBasics:
    def test_default_fields(self):
        e = Envelope(
            from_node="a", to_node="b", kind=EnvelopeKind.SEND, stream_id=0, seq=0
        )
        assert e.payload == b""
        assert e.flags == {}
        assert e.sig is None
        assert e.request_id is None
        assert e.in_reply_to is None

    def test_is_broadcast(self):
        e = Envelope(
            from_node="a", to_node="*", kind=EnvelopeKind.BROADCAST, stream_id=0, seq=0
        )
        assert e.is_broadcast() is True

    def test_is_not_broadcast(self):
        e = Envelope(
            from_node="a", to_node="b", kind=EnvelopeKind.SEND, stream_id=0, seq=0
        )
        assert e.is_broadcast() is False


# ── round-trip encode/decode ─────────────────────────────────────


class TestEnvelopeRoundTrip:
    def test_minimal(self):
        e = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.SEND,
            stream_id=1,
            seq=0,
        )
        out = Envelope.decode(e.encode())
        assert out == e

    def test_with_payload(self):
        e = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.SEND,
            stream_id=1,
            seq=0,
            payload=b"hello",
        )
        out = Envelope.decode(e.encode())
        assert out.payload == b"hello"

    def test_with_sig(self):
        e = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.SEND,
            stream_id=1,
            seq=0,
            sig=b"signature-bytes",
        )
        out = Envelope.decode(e.encode())
        assert out.sig == b"signature-bytes"

    def test_with_flags(self):
        e = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.SEND,
            stream_id=1,
            seq=0,
            flags={"ack_required": True},
        )
        out = Envelope.decode(e.encode())
        assert out.flags == {"ack_required": True}

    def test_with_request_id(self):
        e = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.APP,
            stream_id=1,
            seq=0,
            request_id="req-1",
        )
        out = Envelope.decode(e.encode())
        assert out.request_id == "req-1"

    def test_with_in_reply_to(self):
        e = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.APP,
            stream_id=1,
            seq=0,
            in_reply_to="req-1",
        )
        out = Envelope.decode(e.encode())
        assert out.in_reply_to == "req-1"


# ── decode errors ────────────────────────────────────────────────


class TestDecodeErrors:
    def test_too_short(self):
        with pytest.raises(EnvelopeDecodeError, match="too short"):
            Envelope.decode(b"\x00")

    def test_header_truncated(self):
        # Length prefix says 100 bytes, but body is empty.
        raw = (100).to_bytes(4, "big")
        with pytest.raises(EnvelopeDecodeError, match="truncated"):
            Envelope.decode(raw)

    def test_invalid_msgpack(self):
        # Bytes that decode to msgpack int instead of a dict header.
        bad = b"\xff\xff\xff\xff\xff"
        raw = len(bad).to_bytes(4, "big") + bad
        with pytest.raises(EnvelopeDecodeError):
            Envelope.decode(raw)

    def test_unreadable_msgpack_header_raises_decode_error(self):
        # 0xc1 is a reserved msgpack marker — ``unpack`` raises
        # ValueError. ``decode`` must translate that into its own
        # EnvelopeDecodeError ("not valid msgpack") so callers only
        # ever catch one exception type for a malformed frame.
        bad = b"\xc1"
        raw = len(bad).to_bytes(4, "big") + bad
        with pytest.raises(EnvelopeDecodeError, match="not valid msgpack"):
            Envelope.decode(raw)

    def test_header_not_a_dict(self):
        header = _PACKER.pack([1, 2, 3])  # list, not dict
        raw = len(header).to_bytes(4, "big") + header
        with pytest.raises(EnvelopeDecodeError, match="must be a msgpack map"):
            Envelope.decode(raw)

    def test_missing_required_field(self):
        header = _PACKER.pack({"from": "a"})
        raw = len(header).to_bytes(4, "big") + header
        with pytest.raises(EnvelopeDecodeError, match="missing required fields"):
            Envelope.decode(raw)

    def test_unknown_kind(self):
        header = _PACKER.pack(
            {
                "from": "a",
                "to": "b",
                "kind": "not-a-kind",
                "stream_id": 0,
                "seq": 0,
            }
        )
        raw = len(header).to_bytes(4, "big") + header
        with pytest.raises(EnvelopeDecodeError, match="unknown kind"):
            Envelope.decode(raw)

    def test_stream_id_not_int(self):
        header = _PACKER.pack(
            {
                "from": "a",
                "to": "b",
                "kind": "send",
                "stream_id": "not-int",
                "seq": 0,
            }
        )
        raw = len(header).to_bytes(4, "big") + header
        with pytest.raises(EnvelopeDecodeError, match="integers"):
            Envelope.decode(raw)

    def test_negative_payload_len(self):
        header = _PACKER.pack(
            {
                "from": "a",
                "to": "b",
                "kind": "send",
                "stream_id": 0,
                "seq": 0,
                "payload_len": -1,
            }
        )
        raw = len(header).to_bytes(4, "big") + header
        with pytest.raises(EnvelopeDecodeError, match="non-negative"):
            Envelope.decode(raw)

    def test_body_truncated(self):
        # Header says payload_len=10 but no payload bytes follow.
        header = _PACKER.pack(
            {
                "from": "a",
                "to": "b",
                "kind": "send",
                "stream_id": 0,
                "seq": 0,
                "payload_len": 10,
                "sig_len": 0,
            }
        )
        raw = len(header).to_bytes(4, "big") + header
        with pytest.raises(EnvelopeDecodeError, match="body truncated"):
            Envelope.decode(raw)

    def test_flags_not_dict(self):
        header = _PACKER.pack(
            {
                "from": "a",
                "to": "b",
                "kind": "send",
                "stream_id": 0,
                "seq": 0,
                "flags": [1, 2, 3],
            }
        )
        raw = len(header).to_bytes(4, "big") + header
        with pytest.raises(EnvelopeDecodeError, match="flags must be"):
            Envelope.decode(raw)

    def test_request_id_wrong_type(self):
        header = _PACKER.pack(
            {
                "from": "a",
                "to": "b",
                "kind": "send",
                "stream_id": 0,
                "seq": 0,
                "request_id": 123,
            }
        )
        raw = len(header).to_bytes(4, "big") + header
        with pytest.raises(EnvelopeDecodeError, match="request_id must be"):
            Envelope.decode(raw)


# ── tolerance of unknown header fields ──────────────────────────


class TestForwardCompatible:
    def test_unknown_header_field_tolerated(self):
        header = _PACKER.pack(
            {
                "from": "a",
                "to": "b",
                "kind": "send",
                "stream_id": 0,
                "seq": 0,
                "future_field": "ignored",
            }
        )
        raw = len(header).to_bytes(4, "big") + header
        out = Envelope.decode(raw)
        assert out.from_node == "a"
