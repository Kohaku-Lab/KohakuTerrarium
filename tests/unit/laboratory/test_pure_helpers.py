"""Unit tests for the small pure-helper modules in Laboratory _internal/.

Covers:
- addressing.AddressDirectory
- auth.TokenAuth
- app.{AppMessage, build_app_envelope, parse_app_envelope, new_request_id}
- backpressure.BoundedSendBuffer
- membership.{Membership, MembershipEvent, NodeInfo}
"""

import asyncio

import pytest

from kohakuterrarium.laboratory._internal.addressing import AddressDirectory
from kohakuterrarium.laboratory._internal.app import (
    AppMessageError,
    ExtensionNotFoundError,
    build_app_envelope,
    new_request_id,
    parse_app_envelope,
)
from kohakuterrarium.laboratory._internal.auth import TokenAuth
from kohakuterrarium.laboratory._internal.backpressure import (
    BackpressureError,
    BoundedSendBuffer,
    DEFAULT_BUFFER_SIZE,
)
from kohakuterrarium.laboratory._internal.envelope import Envelope, EnvelopeKind
from kohakuterrarium.laboratory._internal.membership import (
    Membership,
    MembershipEvent,
    NodeInfo,
)
from kohakuterrarium.laboratory._internal.protocol import HelloPayload

# ── AddressDirectory ─────────────────────────────────────────────


class TestAddressDirectory:
    def test_register_resolve_creature(self):
        d = AddressDirectory()
        d.register_creature("ref-1", "node-A")
        assert d.resolve_creature("ref-1") == "node-A"

    def test_unregister_creature(self):
        d = AddressDirectory()
        d.register_creature("ref-1", "node-A")
        assert d.unregister_creature("ref-1") is True
        assert d.resolve_creature("ref-1") is None

    def test_unregister_missing(self):
        d = AddressDirectory()
        assert d.unregister_creature("nope") is False

    def test_known_creatures_copy(self):
        d = AddressDirectory()
        d.register_creature("a", "n1")
        snap = d.known_creatures()
        snap["b"] = "n2"
        # Original unchanged.
        assert d.resolve_creature("b") is None

    def test_register_listener_new(self):
        d = AddressDirectory()
        assert d.register_listener("ch", "n1") is True
        assert d.register_listener("ch", "n1") is False  # already there

    def test_listeners_set(self):
        d = AddressDirectory()
        d.register_listener("ch", "n1")
        d.register_listener("ch", "n2")
        assert d.listeners("ch") == {"n1", "n2"}

    def test_listeners_empty(self):
        d = AddressDirectory()
        assert d.listeners("nope") == set()

    def test_unregister_listener_removes(self):
        d = AddressDirectory()
        d.register_listener("ch", "n1")
        d.register_listener("ch", "n2")
        assert d.unregister_listener("ch", "n1") is True
        assert d.listeners("ch") == {"n2"}

    def test_unregister_listener_drops_empty_channel(self):
        d = AddressDirectory()
        d.register_listener("ch", "n1")
        d.unregister_listener("ch", "n1")
        # Channel removed entirely.
        assert "ch" not in d.known_channels()

    def test_unregister_listener_missing(self):
        d = AddressDirectory()
        assert d.unregister_listener("ch", "n1") is False

    def test_pick_listener_round_robin(self):
        d = AddressDirectory()
        d.register_listener("ch", "n1")
        d.register_listener("ch", "n2")
        # Sorted alphabetically; round-robin index increments.
        first = d.pick_listener("ch")
        second = d.pick_listener("ch")
        third = d.pick_listener("ch")
        # Pick rotates through n1, n2, n1.
        assert first == "n1"
        assert second == "n2"
        assert third == "n1"

    def test_pick_listener_no_listeners(self):
        d = AddressDirectory()
        assert d.pick_listener("ch") is None

    def test_evict_node(self):
        d = AddressDirectory()
        d.register_creature("c1", "n1")
        d.register_creature("c2", "n2")
        d.register_listener("ch1", "n1")
        d.register_listener("ch1", "n2")
        d.register_listener("ch2", "n1")
        c_removed, l_removed = d.evict_node("n1")
        assert c_removed == 1
        assert l_removed == 2
        # n2 still on ch1; ch2 removed entirely.
        assert d.listeners("ch1") == {"n2"}
        assert "ch2" not in d.known_channels()

    def test_known_channels_copy(self):
        d = AddressDirectory()
        d.register_listener("ch", "n1")
        snap = d.known_channels()
        snap["other"] = {"x"}
        assert "other" not in d.known_channels()


# ── TokenAuth ─────────────────────────────────────────────────────


class TestTokenAuth:
    def test_validate_correct(self):
        a = TokenAuth("secret")
        assert a.validate("secret") is True

    def test_validate_wrong(self):
        a = TokenAuth("secret")
        assert a.validate("wrong") is False

    def test_validate_non_str(self):
        a = TokenAuth("secret")
        assert a.validate(None) is False  # type: ignore

    def test_empty_token_disables_auth(self):
        a = TokenAuth("")
        assert a.is_disabled
        # Any input accepted.
        assert a.validate("anything") is True
        assert a.validate(None) is True  # type: ignore

    def test_validate_hello(self):
        a = TokenAuth("secret")
        hello = HelloPayload(
            protocol_version=1,
            framework_version="1.5.0",
            client_name="n",
            token="secret",
            capabilities=(),
        )
        assert a.validate_hello(hello)

    def test_non_str_expected_token_raises(self):
        with pytest.raises(TypeError):
            TokenAuth(123)  # type: ignore


# ── AppMessage / build_app_envelope / parse_app_envelope ─────────


class TestAppMessageHelpers:
    def test_new_request_id_unique(self):
        a = new_request_id()
        b = new_request_id()
        assert a != b

    def test_build_app_envelope_kind(self):
        env = build_app_envelope(
            from_node="a",
            to_node="b",
            namespace="ns",
            type="t",
            body={"x": 1},
        )
        assert env.kind == EnvelopeKind.APP

    def test_round_trip(self):
        env = build_app_envelope(
            from_node="a",
            to_node="b",
            namespace="ns",
            type="t",
            body={"x": 1},
            request_id="req-1",
        )
        msg = parse_app_envelope(env)
        assert msg.namespace == "ns"
        assert msg.type == "t"
        assert msg.body == {"x": 1}
        assert msg.request_id == "req-1"

    def test_parse_wrong_kind_raises(self):
        env = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.SEND,
            stream_id=0,
            seq=0,
            payload=b"",
        )
        with pytest.raises(AppMessageError, match="expected APP envelope"):
            parse_app_envelope(env)

    def test_parse_bad_payload(self):
        env = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.APP,
            stream_id=0,
            seq=0,
            payload=b"\xff\xfe\xfd",
        )
        with pytest.raises(AppMessageError):
            parse_app_envelope(env)

    def test_parse_missing_namespace(self):
        from kohakuvault import DataPacker

        packer = DataPacker("msgpack")
        env = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.APP,
            stream_id=0,
            seq=0,
            payload=packer.pack({"type": "t"}),
        )
        with pytest.raises(AppMessageError, match="namespace"):
            parse_app_envelope(env)

    def test_parse_non_string_required_key_raises(self):
        # ``namespace`` present but not a string — the required-key loop
        # must reject it rather than constructing an AppMessage with a
        # non-str namespace that no extension lookup could ever match.
        from kohakuvault import DataPacker

        packer = DataPacker("msgpack")
        env = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.APP,
            stream_id=0,
            seq=0,
            payload=packer.pack({"namespace": 123, "type": "t"}),
        )
        with pytest.raises(AppMessageError, match="'namespace' must be a string"):
            parse_app_envelope(env)

    def test_parse_unreadable_msgpack_raises(self):
        # A reserved msgpack marker (0xc1) makes ``unpack`` raise
        # ValueError; ``parse_app_envelope`` must translate that to its
        # own AppMessageError rather than leaking the raw decode error.
        env = Envelope(
            from_node="a",
            to_node="b",
            kind=EnvelopeKind.APP,
            stream_id=0,
            seq=0,
            payload=b"\xc1",
        )
        with pytest.raises(AppMessageError, match="not valid msgpack"):
            parse_app_envelope(env)


class TestExceptions:
    def test_extension_not_found(self):
        assert issubclass(ExtensionNotFoundError, LookupError)


# ── BoundedSendBuffer ────────────────────────────────────────────


def _env() -> Envelope:
    return Envelope(
        from_node="a", to_node="b", kind=EnvelopeKind.SEND, stream_id=0, seq=0
    )


class TestBoundedSendBuffer:
    def test_default_maxsize(self):
        b = BoundedSendBuffer()
        assert b.maxsize == DEFAULT_BUFFER_SIZE

    def test_invalid_maxsize_raises(self):
        with pytest.raises(ValueError):
            BoundedSendBuffer(0)
        with pytest.raises(ValueError):
            BoundedSendBuffer(-1)

    async def test_put_get(self):
        b = BoundedSendBuffer(2)
        await b.put(_env())
        await b.put(_env())
        assert b.qsize() == 2
        assert b.is_full()
        first = await b.get()
        assert isinstance(first, Envelope)
        assert b.qsize() == 1

    async def test_put_nowait_full_raises(self):
        b = BoundedSendBuffer(1)
        await b.put(_env())
        with pytest.raises(BackpressureError):
            await b.put(_env(), wait=False)
        assert b.overflow_count == 1

    def test_get_nowait_empty(self):
        b = BoundedSendBuffer(1)
        assert b.get_nowait() is None

    async def test_get_nowait_has_item(self):
        b = BoundedSendBuffer(1)
        await b.put(_env())
        out = b.get_nowait()
        assert isinstance(out, Envelope)


# ── Membership ───────────────────────────────────────────────────


class TestMembership:
    def test_join_new(self):
        m = Membership(heartbeat_timeout_seconds=10.0)
        is_new = m.join("n1", capabilities=("gpu",), now=0.0)
        assert is_new is True
        assert "n1" in m.alive()
        assert m.capabilities("n1") == ("gpu",)

    def test_join_existing_refreshes(self):
        m = Membership()
        m.join("n1", capabilities=("a",), now=0.0)
        assert m.join("n1", capabilities=("b",), now=5.0) is False
        assert m.capabilities("n1") == ("b",)

    def test_heartbeat_known_node(self):
        m = Membership()
        m.join("n1", capabilities=(), now=0.0)
        assert m.heartbeat("n1", now=5.0) is True
        info = m.info("n1")
        assert info.last_heartbeat == 5.0

    def test_heartbeat_unknown(self):
        m = Membership()
        assert m.heartbeat("ghost", now=0.0) is False

    def test_leave_present(self):
        m = Membership()
        m.join("n1", capabilities=(), now=0.0)
        assert m.leave("n1") is True
        assert "n1" not in m.alive()

    def test_leave_unknown(self):
        m = Membership()
        assert m.leave("ghost") is False

    def test_check_lost(self):
        m = Membership(heartbeat_timeout_seconds=10.0)
        m.join("alive", capabilities=(), now=0.0)
        m.join("dead", capabilities=(), now=0.0)
        # Advance time past the alive's heartbeat.
        m.heartbeat("alive", now=20.0)
        lost = m.check_lost(now=20.0)
        assert lost == ["dead"]
        assert "dead" not in m.alive()
        assert "alive" in m.alive()

    def test_capabilities_unknown(self):
        m = Membership()
        assert m.capabilities("ghost") is None

    def test_info_unknown(self):
        m = Membership()
        assert m.info("ghost") is None

    def test_snapshot_copy(self):
        m = Membership()
        m.join("n1", capabilities=(), now=0.0)
        snap = m.snapshot()
        del snap["n1"]
        # Original unchanged.
        assert "n1" in m.alive()

    async def test_subscribe_yields_events(self):
        m = Membership()
        events: list[tuple[MembershipEvent, str]] = []

        async def consume():
            async for evt in m.subscribe():
                events.append(evt)

        task = asyncio.create_task(consume())
        # Give consumer time to register.
        await asyncio.sleep(0)
        m.join("n1", capabilities=(), now=0.0)
        m.leave("n1")
        await asyncio.sleep(0.05)
        m.close_subscribers()
        await asyncio.wait_for(task, timeout=1.0)
        kinds = [e[0] for e in events]
        assert MembershipEvent.JOINED in kinds
        assert MembershipEvent.LEFT in kinds


class TestNodeInfo:
    def test_frozen(self):
        info = NodeInfo(node_id="n", capabilities=(), last_heartbeat=0.0)
        with pytest.raises(Exception):
            info.node_id = "x"  # type: ignore
