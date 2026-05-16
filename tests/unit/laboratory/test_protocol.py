"""Unit tests for :mod:`kohakuterrarium.laboratory._internal.protocol`."""

import pytest
from kohakuvault import DataPacker

from kohakuterrarium.laboratory._internal.envelope import Envelope, EnvelopeKind
from kohakuterrarium.laboratory._internal.protocol import (
    HOST_NODE_ID,
    LAB_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    HelloPayload,
    ProtocolError,
    RejectPayload,
    WelcomePayload,
    build_hello,
    build_reject,
    build_welcome,
    parse_hello,
    parse_reject,
    parse_welcome,
    protocol_compatible,
)

_PACKER = DataPacker("msgpack")


# ── module constants ────────────────────────────────────────────


class TestConstants:
    def test_protocol_version(self):
        assert LAB_PROTOCOL_VERSION == "1.0"

    def test_supported_protocol_versions_includes_current(self):
        assert LAB_PROTOCOL_VERSION in SUPPORTED_PROTOCOL_VERSIONS

    def test_host_node_id(self):
        assert HOST_NODE_ID == "_host"


# ── protocol_compatible ─────────────────────────────────────────


class TestProtocolCompatible:
    def test_known_compatible(self):
        assert protocol_compatible("1.0")

    def test_unknown_incompatible(self):
        assert not protocol_compatible("99.0")

    def test_custom_set(self):
        assert protocol_compatible("2.0", local_supported={"1.0", "2.0"})


# ── HelloPayload ─────────────────────────────────────────────────


def _hello() -> HelloPayload:
    return HelloPayload(
        protocol_version="1.0",
        framework_version="1.5.0",
        client_name="my-client",
        token="secret",
        capabilities=("gpu",),
    )


class TestHelloPayload:
    def test_to_from_dict_round_trip(self):
        h = _hello()
        out = HelloPayload.from_dict(h.to_dict())
        assert out == h

    def test_missing_required_raises(self):
        with pytest.raises(ProtocolError, match="missing required fields"):
            HelloPayload.from_dict({})

    def test_wrong_type_raises(self):
        with pytest.raises(ProtocolError, match="must be a string"):
            HelloPayload.from_dict(
                {
                    "protocol_version": 1,  # int instead of str
                    "framework_version": "1",
                    "client_name": "n",
                    "token": "t",
                }
            )

    def test_capabilities_not_a_list_raises(self):
        with pytest.raises(ProtocolError, match="capabilities must be a list"):
            HelloPayload.from_dict(
                {
                    "protocol_version": "1.0",
                    "framework_version": "1.5.0",
                    "client_name": "n",
                    "token": "t",
                    "capabilities": "not a list",
                }
            )


# ── WelcomePayload ───────────────────────────────────────────────


def _welcome() -> WelcomePayload:
    return WelcomePayload(
        protocol_version="1.0",
        framework_version="1.5.0",
        host_node_id="_host",
        assigned_client_id="my-client",
        supported_verbs=("send", "broadcast"),
        cluster_info={"size": 1},
    )


class TestWelcomePayload:
    def test_round_trip(self):
        w = _welcome()
        out = WelcomePayload.from_dict(w.to_dict())
        assert out == w

    def test_missing_required(self):
        with pytest.raises(ProtocolError):
            WelcomePayload.from_dict({})

    def test_wrong_type(self):
        with pytest.raises(ProtocolError, match="must be a string"):
            WelcomePayload.from_dict(
                {
                    "protocol_version": 1,
                    "framework_version": "x",
                    "host_node_id": "h",
                    "assigned_client_id": "c",
                }
            )

    def test_verbs_not_list(self):
        with pytest.raises(ProtocolError, match="supported_verbs must be a list"):
            WelcomePayload.from_dict(
                {
                    "protocol_version": "1.0",
                    "framework_version": "x",
                    "host_node_id": "h",
                    "assigned_client_id": "c",
                    "supported_verbs": "not-a-list",
                }
            )

    def test_cluster_info_not_dict(self):
        with pytest.raises(ProtocolError, match="cluster_info must be a dict"):
            WelcomePayload.from_dict(
                {
                    "protocol_version": "1.0",
                    "framework_version": "x",
                    "host_node_id": "h",
                    "assigned_client_id": "c",
                    "cluster_info": "junk",
                }
            )


# ── RejectPayload ────────────────────────────────────────────────


class TestRejectPayload:
    def test_round_trip(self):
        r = RejectPayload(reason="auth_failed", detail="bad token")
        out = RejectPayload.from_dict(r.to_dict())
        assert out == r

    def test_missing_reason(self):
        with pytest.raises(ProtocolError, match="missing required field"):
            RejectPayload.from_dict({})

    def test_reason_not_str(self):
        with pytest.raises(ProtocolError, match="reason must be a string"):
            RejectPayload.from_dict({"reason": 1})

    def test_detail_not_str(self):
        with pytest.raises(ProtocolError, match="detail must be a string"):
            RejectPayload.from_dict({"reason": "x", "detail": 1})


# ── envelope helpers ─────────────────────────────────────────────


class TestBuildAndParseHello:
    def test_round_trip(self):
        env = build_hello(_hello())
        assert env.kind == EnvelopeKind.HELLO
        assert env.to_node == HOST_NODE_ID
        out = parse_hello(env)
        assert out == _hello()

    def test_parse_wrong_kind_raises(self):
        env = Envelope(
            from_node="c",
            to_node="h",
            kind=EnvelopeKind.SEND,
            stream_id=0,
            seq=0,
        )
        with pytest.raises(ProtocolError, match="expected HELLO"):
            parse_hello(env)


class TestBuildAndParseWelcome:
    def test_round_trip(self):
        env = build_welcome(_welcome(), to_node="client-1")
        assert env.kind == EnvelopeKind.WELCOME
        assert env.to_node == "client-1"
        out = parse_welcome(env)
        assert out == _welcome()

    def test_parse_wrong_kind(self):
        env = Envelope(
            from_node="h",
            to_node="c",
            kind=EnvelopeKind.SEND,
            stream_id=0,
            seq=0,
        )
        with pytest.raises(ProtocolError, match="expected WELCOME"):
            parse_welcome(env)


class TestBuildAndParseReject:
    def test_round_trip(self):
        r = RejectPayload(reason="auth_failed", detail="bad token")
        env = build_reject(r, to_node="client-1")
        assert env.kind == EnvelopeKind.CONTROL
        out = parse_reject(env)
        assert out == r

    def test_parse_wrong_kind_raises(self):
        env = Envelope(
            from_node="h",
            to_node="c",
            kind=EnvelopeKind.SEND,
            stream_id=0,
            seq=0,
        )
        with pytest.raises(ProtocolError, match="expected CONTROL"):
            parse_reject(env)

    def test_parse_non_reject_control_raises(self):
        # CONTROL envelope but the body's ``control`` field isn't "reject".
        body = _PACKER.pack({"control": "other"})
        env = Envelope(
            from_node="h",
            to_node="c",
            kind=EnvelopeKind.CONTROL,
            stream_id=0,
            seq=0,
            payload=body,
        )
        with pytest.raises(ProtocolError, match="not a reject"):
            parse_reject(env)


class TestDecodePayloadErrors:
    def test_payload_not_dict_raises(self):
        # HELLO envelope but payload is a msgpack list, not a dict.
        body = _PACKER.pack([1, 2, 3])
        env = Envelope(
            from_node="c",
            to_node="h",
            kind=EnvelopeKind.HELLO,
            stream_id=0,
            seq=0,
            payload=body,
        )
        with pytest.raises(ProtocolError, match="must be a msgpack map"):
            parse_hello(env)

    def test_payload_not_msgpack_raises(self):
        env = Envelope(
            from_node="c",
            to_node="h",
            kind=EnvelopeKind.HELLO,
            stream_id=0,
            seq=0,
            payload=b"\xff\xfe\xfd",
        )
        with pytest.raises(ProtocolError):
            parse_hello(env)

    def test_unreadable_msgpack_raises_protocol_error(self):
        # 0xc1 is a reserved msgpack marker that makes ``unpack`` raise
        # ValueError. The payload-decode helper must translate that into
        # a ProtocolError ("not valid msgpack") so the handshake layer
        # sees one error type, not a raw decode exception.
        env = Envelope(
            from_node="c",
            to_node="h",
            kind=EnvelopeKind.HELLO,
            stream_id=0,
            seq=0,
            payload=b"\xc1",
        )
        with pytest.raises(ProtocolError, match="not valid msgpack"):
            parse_hello(env)
