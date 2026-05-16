"""Unit tests for :mod:`kohakuterrarium.terrarium.wire`."""

from pathlib import Path

import pytest

from kohakuterrarium.core.config_types import AgentConfig, InputConfig
from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
    EngineEvent,
    EventFilter,
    EventKind,
)
from kohakuterrarium.terrarium.service import CreatureInfo
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    GraphTopology,
    TopologyDelta,
)
from kohakuterrarium.terrarium.wire import (
    RemoteAddCreatureError,
    _stringify_paths,
    pack_agent_config,
    pack_channel_info,
    pack_connection_result,
    pack_content,
    pack_creature_build_input,
    pack_creature_info,
    pack_disconnection_result,
    pack_engine_event,
    pack_event_filter,
    pack_graph_topology,
    pack_topology_delta,
    unpack_agent_config,
    unpack_channel_info,
    unpack_connection_result,
    unpack_content,
    unpack_creature_build_input,
    unpack_creature_info,
    unpack_disconnection_result,
    unpack_engine_event,
    unpack_event_filter,
    unpack_graph_topology,
    unpack_topology_delta,
)

# ── CreatureInfo round-trip ─────────────────────────────────────


class TestCreatureInfoRoundTrip:
    def test_basic(self):
        c = CreatureInfo(
            creature_id="cid",
            name="n",
            graph_id="g",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=("a", "b"),
            send_channels=("c",),
        )
        out = unpack_creature_info(pack_creature_info(c))
        assert out == c
        assert isinstance(out.listen_channels, tuple)

    def test_tuple_converted_to_list_on_wire(self):
        c = CreatureInfo(
            creature_id="cid",
            name="n",
            graph_id="g",
            is_running=False,
            is_privileged=True,
            parent_creature_id="parent",
            listen_channels=("x",),
            send_channels=(),
        )
        packed = pack_creature_info(c)
        assert isinstance(packed["listen_channels"], list)


# ── ChannelInfo round-trip ──────────────────────────────────────


class TestChannelInfoRoundTrip:
    def test_basic(self):
        c = ChannelInfo(name="ch", description="d")
        out = unpack_channel_info(pack_channel_info(c))
        assert out == c

    def test_missing_description_default(self):
        out = unpack_channel_info({"name": "ch"})
        assert out.description == ""


# ── GraphTopology round-trip ────────────────────────────────────


class TestGraphTopologyRoundTrip:
    def test_basic(self):
        g = GraphTopology(
            graph_id="g1",
            creature_ids={"c1", "c2"},
            channels={"ch": ChannelInfo(name="ch")},
            listen_edges={"c1": {"ch"}, "c2": set()},
            send_edges={"c1": set(), "c2": {"ch"}},
        )
        out = unpack_graph_topology(pack_graph_topology(g))
        assert out.graph_id == g.graph_id
        assert out.creature_ids == g.creature_ids
        assert "ch" in out.channels

    def test_minimal(self):
        g = GraphTopology(graph_id="g1")
        out = unpack_graph_topology(pack_graph_topology(g))
        assert out.graph_id == "g1"
        assert out.creature_ids == set()


# ── TopologyDelta round-trip ────────────────────────────────────


class TestTopologyDeltaRoundTrip:
    def test_basic(self):
        d = TopologyDelta(
            kind="merge",
            old_graph_ids=["a", "b"],
            new_graph_ids=["a"],
            affected_creatures={"c1", "c2"},
        )
        out = unpack_topology_delta(pack_topology_delta(d))
        assert out == d


# ── ConnectionResult / DisconnectionResult ──────────────────────


class TestConnectionResult:
    def test_round_trip(self):
        r = ConnectionResult(
            channel="ch",
            trigger_id="t1",
            delta_kind="nothing",
            graph_id="g",
        )
        out = unpack_connection_result(pack_connection_result(r))
        assert out == r

    def test_missing_fields_defaults(self):
        out = unpack_connection_result({"channel": "ch"})
        assert out.channel == "ch"
        assert out.trigger_id == ""


class TestDisconnectionResult:
    def test_round_trip(self):
        r = DisconnectionResult(channels=["ch"], delta_kind="split")
        out = unpack_disconnection_result(pack_disconnection_result(r))
        assert out == r


# ── EngineEvent / EventFilter ───────────────────────────────────


class TestEngineEventRoundTrip:
    def test_round_trip(self):
        e = EngineEvent(
            kind=EventKind.CREATURE_STARTED,
            creature_id="c1",
            graph_id="g",
            channel=None,
            payload={"x": 1},
            ts=10.0,
        )
        out = unpack_engine_event(pack_engine_event(e))
        assert out.kind == e.kind
        assert out.creature_id == e.creature_id
        assert out.payload == {"x": 1}


class TestEventFilterRoundTrip:
    def test_none_returns_none(self):
        assert pack_event_filter(None) is None
        assert unpack_event_filter(None) is None

    def test_round_trip_full(self):
        f = EventFilter(
            kinds={EventKind.CREATURE_STARTED, EventKind.CREATURE_STOPPED},
            creature_ids={"c1", "c2"},
            graph_ids={"g1"},
            channels={"ch"},
        )
        out = unpack_event_filter(pack_event_filter(f))
        assert out.kinds == f.kinds
        assert out.creature_ids == f.creature_ids
        assert out.graph_ids == f.graph_ids
        assert out.channels == f.channels

    def test_round_trip_partial(self):
        f = EventFilter(kinds={EventKind.CREATURE_STARTED})
        out = unpack_event_filter(pack_event_filter(f))
        assert out.kinds == f.kinds
        assert out.creature_ids is None


# ── AgentConfig pack / unpack ───────────────────────────────────


class TestAgentConfigRoundTrip:
    def test_basic(self):
        c = AgentConfig(name="alice", agent_path=Path("/some/path"))
        packed = pack_agent_config(c)
        # Path → str on the wire (str(Path) is OS-native).
        assert packed["agent_path"] == str(Path("/some/path"))
        out = unpack_agent_config(packed)
        assert out.name == "alice"
        # Path reconstructed.
        assert isinstance(out.agent_path, Path)

    def test_nested_input_config(self):
        c = AgentConfig(
            name="x",
            agent_path=Path("."),
            input=InputConfig(type="cli"),
        )
        out = unpack_agent_config(pack_agent_config(c))
        assert out.input.type == "cli"

    def test_unknown_keys_dropped(self):
        d = pack_agent_config(AgentConfig(name="x", agent_path=Path(".")))
        d["future_field"] = "ignored"
        out = unpack_agent_config(d)
        assert out.name == "x"


# ── _stringify_paths ─────────────────────────────────────────────


class TestStringifyPaths:
    def test_path_to_str(self):
        # str(Path(...)) is OS-native (backslashes on Windows).
        expected = str(Path("/x"))
        assert _stringify_paths(Path("/x")) == expected

    def test_dict_recurses(self):
        out = _stringify_paths({"a": Path("/x"), "b": [Path("/y")]})
        assert out["a"] == str(Path("/x"))
        assert out["b"] == [str(Path("/y"))]

    def test_tuple_becomes_list(self):
        out = _stringify_paths((Path("/x"), 1))
        assert out == [str(Path("/x")), 1]

    def test_primitives_passthrough(self):
        assert _stringify_paths(42) == 42
        assert _stringify_paths("s") == "s"
        assert _stringify_paths(None) is None


# ── pack_creature_build_input ───────────────────────────────────


class TestPackCreatureBuildInput:
    def test_agent_config_form(self):
        c = AgentConfig(name="x", agent_path=Path("."))
        out = pack_creature_build_input(c)
        assert out["kind"] == "agent_config"
        assert "value" in out

    def test_absolute_path_string(self):
        out = pack_creature_build_input("/abs/path")
        assert out == {"kind": "path", "value": "/abs/path"}

    def test_windows_drive_path(self):
        out = pack_creature_build_input("C:\\some\\path")
        assert out["kind"] == "path"
        assert out["value"] == "C:\\some\\path"

    def test_relative_path_rejected(self):
        with pytest.raises(RemoteAddCreatureError, match="absolute"):
            pack_creature_build_input("relative/path")

    def test_pathlib_absolute(self):
        # Pathlib absolute paths are also valid.
        path = Path("/abs/x").absolute()
        out = pack_creature_build_input(str(path))
        assert out["kind"] == "path"

    def test_unsupported_type(self):
        with pytest.raises(RemoteAddCreatureError, match="unsupported"):
            pack_creature_build_input(42)


# ── unpack_creature_build_input ─────────────────────────────────


class TestUnpackCreatureBuildInput:
    def test_path_kind(self):
        out = unpack_creature_build_input({"kind": "path", "value": "/x"})
        assert out == "/x"

    def test_agent_config_kind(self):
        c = AgentConfig(name="x", agent_path=Path("."))
        packed = pack_creature_build_input(c)
        out = unpack_creature_build_input(packed)
        assert isinstance(out, AgentConfig)
        assert out.name == "x"

    def test_unknown_kind(self):
        with pytest.raises(RemoteAddCreatureError):
            unpack_creature_build_input({"kind": "weird"})


# ── pack_content / unpack_content ───────────────────────────────


class TestPackContent:
    def test_string(self):
        assert pack_content("hi") == "hi"

    def test_list_of_dicts(self):
        out = pack_content([{"type": "text", "text": "hi"}])
        assert out == [{"type": "text", "text": "hi"}]

    def test_list_strips_paths(self):
        out = pack_content([{"type": "image", "path": Path("/x")}])
        # str(Path(...)) is OS-native.
        assert out[0]["path"] == str(Path("/x"))

    def test_non_dict_part_rejected(self):
        with pytest.raises(TypeError):
            pack_content(["not a dict"])  # type: ignore

    def test_unsupported_type(self):
        with pytest.raises(TypeError):
            pack_content(42)  # type: ignore

    def test_unpack_passthrough(self):
        assert unpack_content("hi") == "hi"
