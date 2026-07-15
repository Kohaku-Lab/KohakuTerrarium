from types import SimpleNamespace

import pytest

from kohakuterrarium.core.config import AgentConfig
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.errors import (
    GraphManifestError,
    GraphManifestVersionError,
    SessionNotResumableError,
    SessionNotResumableError,
)
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.graph_manifest import (
    MANIFEST_KEY,
    GraphManifest,
    capture_manifest,
    checkpoint_graph,
    load_manifest,
    manifest_to_dict,
    parse_manifest,
    save_manifest,
)
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    GraphTopology,
    TopologyState,
)


def _creature_row(
    creature_id="creature_a",
    name="alice",
    *,
    parent_creature_id=None,
    pwd="/work",
):
    return {
        "creature_id": creature_id,
        "name": name,
        "config_snapshot": pack_agent_config(AgentConfig(name=name)),
        "source_ref": f"@pkg/{name}",
        "pwd": pwd,
        "is_privileged": False,
        "parent_creature_id": parent_creature_id,
    }


def _raw_manifest(**overrides):
    raw = {
        "kind": "kohakuterrarium.live_graph",
        "version": 1,
        "revision": 2,
        "graph_id": "graph_test",
        "creatures": [_creature_row()],
        "channels": [{"name": "tasks", "description": "Work"}],
        "listen": [["creature_a", "tasks"]],
        "send": [["creature_a", "tasks"]],
    }
    raw.update(overrides)
    return raw


class _Store:
    def __init__(self):
        self.meta = {}
        self._path = "test.kohakutr"

    def load_meta(self):
        return dict(self.meta)


class _Engine:
    def __init__(self, graph, creatures):
        self._topology = TopologyState(
            graphs={graph.graph_id: graph},
            creature_to_graph={cid: graph.graph_id for cid in graph.creature_ids},
        )
        self._creatures = creatures
        self._session_stores = {}

    def get_graph(self, graph_id):
        return self._topology.graphs[graph_id]

    def get_creature(self, creature_id):
        return self._creatures[creature_id]


def _runtime_creature(
    creature_id, name, *, pwd, source_ref, privileged=False, parent=None
):
    config = AgentConfig(name=name)
    agent = SimpleNamespace(config=config)
    return Creature(
        creature_id=creature_id,
        name=name,
        agent=agent,
        graph_id="graph_test",
        config=config,
        config_snapshot=pack_agent_config(config),
        source_ref=source_ref,
        build_pwd=pwd,
        is_privileged=privileged,
        parent_creature_id=parent,
    )


class TestGraphManifest:
    def test_round_trip_and_canonical_sort(self):
        raw = _raw_manifest(
            creatures=[
                _creature_row("creature_b", "bob"),
                _creature_row("creature_a", "alice"),
            ],
            channels=[
                {"name": "zeta", "description": ""},
                {"name": "alpha", "description": "A"},
            ],
            listen=[["creature_b", "zeta"], ["creature_a", "alpha"]],
            send=[["creature_b", "alpha"], ["creature_a", "zeta"]],
        )

        payload = manifest_to_dict(parse_manifest(raw))

        assert [row["creature_id"] for row in payload["creatures"]] == [
            "creature_a",
            "creature_b",
        ]
        assert [row["name"] for row in payload["channels"]] == ["alpha", "zeta"]
        assert payload["listen"] == [
            ["creature_a", "alpha"],
            ["creature_b", "zeta"],
        ]
        assert manifest_to_dict(parse_manifest(payload)) == payload

    @pytest.mark.parametrize(
        ("change", "field"),
        [
            ({"kind": "other"}, "kind"),
            ({"version": 2}, "version"),
            ({"revision": -1}, "revision"),
            ({"graph_id": ""}, "graph_id"),
            ({"creatures": []}, "creatures"),
        ],
    )
    def test_rejects_invalid_top_level_fields(self, change, field):
        error_type = (
            GraphManifestVersionError if field == "version" else GraphManifestError
        )
        with pytest.raises(error_type) as raised:
            parse_manifest(_raw_manifest(**change))
        assert raised.value.field == field

    @pytest.mark.parametrize("duplicate_field", ["creature_id", "name"])
    def test_rejects_duplicate_creature_identity(self, duplicate_field):
        first = _creature_row()
        second = _creature_row("creature_b", "bob")
        second[duplicate_field] = first[duplicate_field]
        with pytest.raises(GraphManifestError, match="duplicate value"):
            parse_manifest(_raw_manifest(creatures=[first, second]))

    @pytest.mark.parametrize(
        "change",
        [
            {"channels": [{"name": "tasks"}, {"name": "tasks"}]},
            {"listen": [["missing", "tasks"]]},
            {"send": [["creature_a", "missing"]]},
            {"listen": [["creature_a", "tasks"], ["creature_a", "tasks"]]},
            {"creatures": [_creature_row(parent_creature_id="creature_missing")]},
        ],
    )
    def test_rejects_dangling_and_duplicate_relationships(self, change):
        with pytest.raises(GraphManifestError):
            parse_manifest(_raw_manifest(**change))

    def test_rejects_self_parent_and_parent_cycle(self):
        with pytest.raises(GraphManifestError, match="own parent"):
            parse_manifest(
                _raw_manifest(
                    creatures=[_creature_row(parent_creature_id="creature_a")]
                )
            )

        with pytest.raises(GraphManifestError, match="parent cycle"):
            parse_manifest(
                _raw_manifest(
                    creatures=[
                        _creature_row(
                            "creature_a", "alice", parent_creature_id="creature_b"
                        ),
                        _creature_row(
                            "creature_b", "bob", parent_creature_id="creature_a"
                        ),
                    ],
                    listen=[],
                    send=[],
                )
            )

    def test_rejects_invalid_config_snapshot(self):
        row = _creature_row()
        row["config_snapshot"] = {"unknown": "field"}
        with pytest.raises(GraphManifestError) as raised:
            parse_manifest(_raw_manifest(creatures=[row]))
        assert raised.value.field == "creatures[0].config_snapshot"

    def test_capture_records_provenance_topology_and_empty_channel(self):
        root = _runtime_creature(
            "creature_root",
            "root",
            pwd="/workspace/root",
            source_ref="@pkg/root",
            privileged=True,
        )
        child = _runtime_creature(
            "creature_child",
            "child",
            pwd="/workspace/child",
            source_ref="/configs/child",
            parent="creature_root",
        )
        graph = GraphTopology(
            graph_id="graph_test",
            creature_ids={"creature_root", "creature_child"},
            channels={
                "empty": ChannelInfo("empty", "No edges"),
                "tasks": ChannelInfo("tasks", "Work"),
            },
            listen_edges={
                "creature_root": {"tasks"},
                "creature_child": set(),
            },
            send_edges={
                "creature_root": set(),
                "creature_child": {"tasks"},
            },
        )
        engine = _Engine(
            graph,
            {"creature_root": root, "creature_child": child},
        )

        payload = manifest_to_dict(capture_manifest(engine, "graph_test", revision=4))

        assert payload["revision"] == 4
        assert payload["channels"] == [
            {"name": "empty", "description": "No edges"},
            {"name": "tasks", "description": "Work"},
        ]
        assert payload["listen"] == [["creature_root", "tasks"]]
        assert payload["send"] == [["creature_child", "tasks"]]
        rows = {row["name"]: row for row in payload["creatures"]}
        assert rows["root"]["is_privileged"] is True
        assert rows["root"]["pwd"] == "/workspace/root"
        assert rows["root"]["source_ref"] == "@pkg/root"
        assert rows["child"]["parent_creature_id"] == "creature_root"

    def test_capture_syncs_current_agent_config(self):
        creature = _runtime_creature(
            "creature_a",
            "alice",
            pwd="/work",
            source_ref=None,
        )
        creature.agent.config.output_wiring = []
        stale = pack_agent_config(AgentConfig(name="stale"))
        creature.config_snapshot = stale
        graph = GraphTopology(
            graph_id="graph_test",
            creature_ids={"creature_a"},
            listen_edges={"creature_a": set()},
            send_edges={"creature_a": set()},
        )
        engine = _Engine(graph, {"creature_a": creature})

        captured = capture_manifest(engine, "graph_test")

        assert captured.creatures[0].unpack_config().name == "alice"
        assert creature.config_snapshot["name"] == "alice"

    @pytest.mark.parametrize("missing", ["config_snapshot", "build_pwd"])
    def test_capture_rejects_unsupported_provenance(self, missing):
        creature = _runtime_creature(
            "creature_a",
            "alice",
            pwd="/work",
            source_ref=None,
        )
        setattr(creature, missing, None if missing == "config_snapshot" else "")
        graph = GraphTopology(
            graph_id="graph_test",
            creature_ids={"creature_a"},
            listen_edges={"creature_a": set()},
            send_edges={"creature_a": set()},
        )
        engine = _Engine(graph, {"creature_a": creature})

        with pytest.raises(SessionNotResumableError, match="alice"):
            capture_manifest(engine, "graph_test")

    def test_runtime_injection_rejects_persisted_capture(self):
        graph = GraphTopology(
            graph_id="graph_test",
            creature_ids={"creature_a"},
            channels={},
            listen_edges={"creature_a": set()},
            send_edges={"creature_a": set()},
        )
        creature = _runtime_creature(
            "creature_a", "alice", pwd="/work", source_ref="@pack/alice"
        )
        creature.injected_runtime = ("tools",)

        with pytest.raises(SessionNotResumableError, match="runtime injection"):
            capture_manifest(_Engine(graph, {"creature_a": creature}), "graph_test")

    def test_load_save_and_checkpoint_revision(self):
        store = _Store()
        save_manifest(store, parse_manifest(_raw_manifest()))
        assert load_manifest(store).revision == 2

        creature = _runtime_creature(
            "creature_a",
            "alice",
            pwd="/work",
            source_ref="@pkg/alice",
        )
        graph = GraphTopology(
            graph_id="graph_test",
            creature_ids={"creature_a"},
            listen_edges={"creature_a": set()},
            send_edges={"creature_a": set()},
        )
        engine = _Engine(graph, {"creature_a": creature})
        engine._session_stores["graph_test"] = store

        manifest = checkpoint_graph(engine, "graph_test")

        assert isinstance(manifest, GraphManifest)
        assert manifest.revision == 3
        assert store.meta[MANIFEST_KEY]["revision"] == 3

    def test_absent_load_and_unpersisted_checkpoint_are_noops(self):
        store = _Store()
        assert load_manifest(store) is None

        # The checkpoint tombstone (key present, value None) reads as
        # absent — resume must fall back to the legacy paths, not parse it.
        store.meta[MANIFEST_KEY] = None
        assert load_manifest(store) is None

        graph = GraphTopology(graph_id="graph_test")
        engine = _Engine(graph, {})
        assert checkpoint_graph(engine, "graph_test") is None
