"""Unit tests for :mod:`kohakuterrarium.laboratory._internal.control`."""

import pytest
from kohakuvault import DataPacker

from kohakuterrarium.laboratory._internal.control import (
    ControlError,
    RegisterCreaturePayload,
    SubscribePayload,
    build_register_creature,
    build_subscribe,
    build_unregister_creature,
    build_unsubscribe,
    parse_control,
)
from kohakuterrarium.laboratory._internal.envelope import Envelope, EnvelopeKind

_PACKER = DataPacker("msgpack")


# ── Dataclasses ──────────────────────────────────────────────────


class TestPayloads:
    def test_subscribe_frozen(self):
        s = SubscribePayload(channel="x")
        with pytest.raises(Exception):
            s.channel = "y"  # type: ignore

    def test_register_creature(self):
        r = RegisterCreaturePayload(ref="r")
        assert r.ref == "r"


# ── builders ─────────────────────────────────────────────────────


class TestBuilders:
    def test_build_subscribe(self):
        env = build_subscribe(from_node="c", to_node="h", channel="ch")
        assert env.kind == EnvelopeKind.CONTROL
        kind, fields = parse_control(env)
        assert kind == "subscribe"
        assert fields == {"channel": "ch"}

    def test_build_unsubscribe(self):
        env = build_unsubscribe(from_node="c", to_node="h", channel="ch")
        kind, fields = parse_control(env)
        assert kind == "unsubscribe"
        assert fields == {"channel": "ch"}

    def test_build_register_creature(self):
        env = build_register_creature(from_node="c", to_node="h", ref="r")
        kind, fields = parse_control(env)
        assert kind == "register_creature"
        assert fields == {"ref": "r"}

    def test_build_unregister_creature(self):
        env = build_unregister_creature(from_node="c", to_node="h", ref="r")
        kind, fields = parse_control(env)
        assert kind == "unregister_creature"


# ── parse_control errors ────────────────────────────────────────


class TestParseControl:
    def test_wrong_kind(self):
        env = Envelope(
            from_node="c",
            to_node="h",
            kind=EnvelopeKind.SEND,
            stream_id=0,
            seq=0,
        )
        with pytest.raises(ControlError, match="expected CONTROL"):
            parse_control(env)

    def test_bad_payload(self):
        env = Envelope(
            from_node="c",
            to_node="h",
            kind=EnvelopeKind.CONTROL,
            stream_id=0,
            seq=0,
            payload=b"\xff\xfe",
        )
        with pytest.raises(ControlError):
            parse_control(env)

    def test_unreadable_msgpack_raises_control_error(self):
        # 0xc1 is a reserved msgpack marker — ``unpack`` raises
        # ValueError; ``parse_control`` must surface that as its own
        # ControlError ("not valid msgpack"), not leak the raw error.
        env = Envelope(
            from_node="c",
            to_node="h",
            kind=EnvelopeKind.CONTROL,
            stream_id=0,
            seq=0,
            payload=b"\xc1",
        )
        with pytest.raises(ControlError, match="not valid msgpack"):
            parse_control(env)

    def test_non_dict_body(self):
        env = Envelope(
            from_node="c",
            to_node="h",
            kind=EnvelopeKind.CONTROL,
            stream_id=0,
            seq=0,
            payload=_PACKER.pack([1, 2, 3]),
        )
        with pytest.raises(ControlError, match="must be a map"):
            parse_control(env)

    def test_missing_control_key(self):
        env = Envelope(
            from_node="c",
            to_node="h",
            kind=EnvelopeKind.CONTROL,
            stream_id=0,
            seq=0,
            payload=_PACKER.pack({"x": 1}),
        )
        with pytest.raises(ControlError, match="missing 'control'"):
            parse_control(env)

    def test_non_string_control_key(self):
        env = Envelope(
            from_node="c",
            to_node="h",
            kind=EnvelopeKind.CONTROL,
            stream_id=0,
            seq=0,
            payload=_PACKER.pack({"control": 1}),
        )
        with pytest.raises(ControlError, match="missing 'control'"):
            parse_control(env)
