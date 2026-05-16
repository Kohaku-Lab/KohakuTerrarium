"""Unit tests for :mod:`kohakuterrarium.terrarium.wiring`."""

import pytest

from kohakuterrarium.core.output_wiring import OutputWiringEntry
from kohakuterrarium.terrarium.wiring import (
    _coerce_output_entry,
    _short_hash,
    _slug,
    add_output_edge,
    add_secondary_sink,
    install_output_wiring_resolver,
    list_output_edges,
    output_edge_id,
    output_edge_to_dict,
    remove_output_edge,
    remove_secondary_sink,
)

# ── fakes ─────────────────────────────────────────────────────────


class _FakeConfig:
    def __init__(self):
        self.output_wiring: list[OutputWiringEntry] = []


class _FakeAgent:
    def __init__(self):
        self.config = _FakeConfig()
        self._wiring_resolver = None
        self._creature_id = None


class _FakeRouter:
    def __init__(self):
        self._secondary_outputs: list = []

    def add_secondary(self, sink):
        self._secondary_outputs.append(sink)

    def remove_secondary(self, sink):
        if sink in self._secondary_outputs:
            self._secondary_outputs.remove(sink)


class _FakeAgentWithRouter(_FakeAgent):
    def __init__(self):
        super().__init__()
        self.output_router = _FakeRouter()


class _FakeCreature:
    def __init__(self, agent, creature_id=None):
        self.agent = agent
        self.creature_id = creature_id


# ── _slug / _short_hash ──────────────────────────────────────────


class TestSlug:
    def test_basic(self):
        assert _slug("alice") == "alice"

    def test_strips_specials(self):
        assert _slug("a/b/c") == "a_b_c"

    def test_keeps_hyphens_underscores(self):
        assert _slug("a-b_c") == "a-b_c"

    def test_empty(self):
        assert _slug("") == "target"


class TestShortHash:
    def test_deterministic(self):
        assert _short_hash("hi") == _short_hash("hi")

    def test_different_inputs_distinct(self):
        assert _short_hash("a") != _short_hash("b")

    def test_format(self):
        h = _short_hash("x")
        assert len(h) == 8
        assert all(c in "0123456789abcdef" for c in h)


# ── _coerce_output_entry ─────────────────────────────────────────


class TestCoerceOutputEntry:
    def test_entry_passthrough(self):
        e = OutputWiringEntry(to="x")
        assert _coerce_output_entry(e) is e

    def test_string_to_entry(self):
        e = _coerce_output_entry("alice")
        assert isinstance(e, OutputWiringEntry)
        assert e.to == "alice"

    def test_dict_to_entry(self):
        e = _coerce_output_entry({"to": "bob", "with_content": False})
        assert e.to == "bob"
        assert e.with_content is False


# ── output_edge_id / output_edge_to_dict ─────────────────────────


class TestOutputEdgeId:
    def test_deterministic(self):
        e = OutputWiringEntry(to="alice")
        assert output_edge_id(e) == output_edge_id(e)

    def test_starts_with_wire_prefix(self):
        e = OutputWiringEntry(to="alice")
        assert output_edge_id(e).startswith("wire_")

    def test_distinct_for_different_entries(self):
        a = OutputWiringEntry(to="alice")
        b = OutputWiringEntry(to="bob")
        assert output_edge_id(a) != output_edge_id(b)


class TestOutputEdgeToDict:
    def test_basic(self):
        e = OutputWiringEntry(to="alice", prompt="hello", with_content=False)
        d = output_edge_to_dict(e)
        assert d["to"] == "alice"
        assert d["prompt"] == "hello"
        assert d["with_content"] is False
        assert d["id"].startswith("wire_")


# ── add/remove/list output_edge ──────────────────────────────────


class TestOutputEdgeAPI:
    def test_add_string(self):
        a = _FakeAgent()
        eid = add_output_edge(a, "bob")
        assert len(a.config.output_wiring) == 1
        assert a.config.output_wiring[0].to == "bob"
        assert eid.startswith("wire_")

    def test_add_entry_object(self):
        a = _FakeAgent()
        e = OutputWiringEntry(to="bob", with_content=False)
        eid = add_output_edge(a, e)
        assert a.config.output_wiring[0] is e
        assert eid

    def test_remove_existing(self):
        a = _FakeAgent()
        eid = add_output_edge(a, "bob")
        ok = remove_output_edge(a, eid)
        assert ok is True
        assert a.config.output_wiring == []

    def test_remove_missing(self):
        a = _FakeAgent()
        ok = remove_output_edge(a, "wire_does_not_exist")
        assert ok is False

    def test_list(self):
        a = _FakeAgent()
        add_output_edge(a, "alice")
        add_output_edge(a, "bob")
        out = list_output_edges(a)
        assert len(out) == 2
        targets = [e["to"] for e in out]
        assert targets == ["alice", "bob"]

    def test_no_config_raises(self):
        a = _FakeAgent()
        a.config = None
        with pytest.raises(AttributeError, match="no config"):
            add_output_edge(a, "x")

    def test_config_without_output_wiring_attr(self):
        class _C:
            pass

        a = _FakeAgent()
        a.config = _C()
        # No output_wiring attribute → auto-created.
        add_output_edge(a, "x")
        assert getattr(a.config, "output_wiring", None) is not None


# ── install_output_wiring_resolver ───────────────────────────────


class TestInstallResolver:
    def test_installs_on_every_creature(self):
        agent_a = _FakeAgent()
        agent_b = _FakeAgent()
        creatures = {
            "alice": _FakeCreature(agent_a, "cid_a"),
            "bob": _FakeCreature(agent_b, "cid_b"),
        }

        class _FakeEngine:
            pass

        engine = _FakeEngine()
        engine._creatures = creatures
        resolver = install_output_wiring_resolver(engine)
        assert agent_a._wiring_resolver is resolver
        assert agent_b._wiring_resolver is resolver
        assert agent_a._creature_id == "cid_a"


# ── add/remove secondary sink ────────────────────────────────────


class _FakeSink:
    pass


class TestSecondarySink:
    def test_add_returns_id(self):
        a = _FakeAgentWithRouter()
        sink = _FakeSink()
        sid = add_secondary_sink(a, sink)
        assert sid.startswith("sink_")
        assert sink in a.output_router._secondary_outputs

    def test_remove_existing(self):
        a = _FakeAgentWithRouter()
        sink = _FakeSink()
        sid = add_secondary_sink(a, sink)
        ok = remove_secondary_sink(a, sid)
        assert ok is True
        assert sink not in a.output_router._secondary_outputs

    def test_remove_missing(self):
        a = _FakeAgentWithRouter()
        ok = remove_secondary_sink(a, "sink_deadbeef")
        assert ok is False

    def test_remove_falls_back_to_list_mutation(self):
        class _RouterNoRemove:
            def __init__(self):
                self._secondary_outputs = []

            def add_secondary(self, sink):
                self._secondary_outputs.append(sink)

        a = _FakeAgent()
        a.output_router = _RouterNoRemove()
        sink = _FakeSink()
        sid = add_secondary_sink(a, sink)
        ok = remove_secondary_sink(a, sid)
        assert ok is True
        assert sink not in a.output_router._secondary_outputs
