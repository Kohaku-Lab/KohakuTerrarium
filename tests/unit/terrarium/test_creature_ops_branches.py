"""Branch-coverage tests for :mod:`kohakuterrarium.terrarium.creature_ops`.

Targets the defensive arms and the graph-snapshot serialisation paths
(``_creatures_for_graph`` root resolution, ``_output_edges_for_graph``,
``chat_history_for`` fallback) the happy-path suite doesn't reach.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium import creature_ops as co
from kohakuterrarium.terrarium.topology import GraphTopology

# ---------------------------------------------------------------------------
# native tool inventory — schema_fn raising
# ---------------------------------------------------------------------------


class TestNativeToolInventoryBranches:
    def test_schema_fn_exception_yields_empty_schema(self):
        """A provider-native tool whose ``provider_native_option_schema``
        raises still appears in the inventory — with an empty schema."""

        class _Tool:
            is_provider_native = True
            description = "d"

            @staticmethod
            def provider_native_option_schema():
                raise RuntimeError("schema build failed")

        class _Registry:
            def list_tools(self):
                return ["x"]

            def get_tool(self, name):
                return _Tool()

        agent = SimpleNamespace(registry=_Registry(), native_tool_options=None)
        out = co.agent_native_tool_inventory(agent)
        assert out[0]["name"] == "x"
        # Schema generation failed → empty dict, not a crash.
        assert out[0]["option_schema"] == {}


# ---------------------------------------------------------------------------
# agent_list_plugins — list_fn not callable
# ---------------------------------------------------------------------------


class TestListPluginsBranches:
    def test_non_callable_list_fn_returns_empty(self):
        """A plugin manager whose ``list_plugins`` is not callable yields
        an empty list rather than crashing."""
        pm = SimpleNamespace(list_plugins="not a function", list="also not")
        agent = SimpleNamespace(plugins=pm)
        assert co.agent_list_plugins(agent) == []


# ---------------------------------------------------------------------------
# agent_get_plugin_options — option_schema raising
# ---------------------------------------------------------------------------


class TestGetPluginOptionsBranches:
    def test_option_schema_exception_yields_empty_schema(self):
        class _Plugin:
            @staticmethod
            def option_schema():
                raise RuntimeError("schema broke")

            def get_options(self):
                return {"k": "v"}

        class _PM:
            def get_plugin(self, name):
                return _Plugin()

        agent = SimpleNamespace(plugins=_PM())
        out = co.agent_get_plugin_options(agent, "p1")
        # Schema failed → empty; options still surfaced.
        assert out["schema"] == {}
        assert out["options"] == {"k": "v"}


# ---------------------------------------------------------------------------
# agent_toggle_plugin — load_pending hook on enable
# ---------------------------------------------------------------------------


class TestTogglePluginLoadPending:
    async def test_enable_invokes_load_pending(self):
        """Enabling a plugin whose manager exposes ``load_pending``
        awaits it so the plugin actually loads."""
        loaded = []

        class _PM:
            """Faithful ``PluginManager`` stand-in: ``get_plugin`` returns
            the registered plugin (or ``None`` for an unknown name), so
            ``agent_toggle_plugin``'s unknown-plugin guard sees a real
            registration — same contract as the real manager."""

            def __init__(self):
                self._registered = {"p1": object()}
                self._enabled = set()

            def get_plugin(self, name):
                return self._registered.get(name)

            def is_enabled(self, name):
                return name in self._enabled

            def enable(self, name):
                self._enabled.add(name)

            def disable(self, name):
                self._enabled.discard(name)

            async def load_pending(self):
                loaded.append(True)

        agent = SimpleNamespace(plugins=_PM())
        out = await co.agent_toggle_plugin(agent, "p1")
        assert out["enabled"] is True
        assert loaded == [True]


# ---------------------------------------------------------------------------
# agent_set_module_options — plugin + native_tool dispatch
# ---------------------------------------------------------------------------


class TestSetModuleOptionsDispatch:
    def test_plugin_dispatch(self):
        store = {}

        class _Helper:
            def set(self, name, values):
                store[name] = values
                return values

        agent = SimpleNamespace(plugin_options=_Helper())
        out = co.agent_set_module_options(agent, "plugin", "p1", {"k": "v"})
        assert out == {"k": "v"}
        assert store == {"p1": {"k": "v"}}

    def test_native_tool_dispatch(self):
        store = {}

        class _Helper:
            def set(self, name, values):
                store[name] = values
                return values

        agent = SimpleNamespace(native_tool_options=_Helper())
        out = co.agent_set_module_options(agent, "native_tool", "t1", {"a": 1})
        assert out == {"a": 1}
        assert store == {"t1": {"a": 1}}


# ---------------------------------------------------------------------------
# _resumable_events — store raising
# ---------------------------------------------------------------------------


class TestResumableEventsBranches:
    def test_store_exception_yields_empty(self):
        class _Store:
            def get_resumable_events(self, name, live_job_ids=None):
                raise RuntimeError("store down")

        assert co._resumable_events(_Store(), "alice", set()) == []

    def test_store_returns_events(self):
        class _Store:
            def get_resumable_events(self, name, live_job_ids=None):
                return [{"e": 1}]

        assert co._resumable_events(_Store(), "alice", set()) == [{"e": 1}]


# ---------------------------------------------------------------------------
# chat_history_for — agent-store hit + engine-store fallback
# ---------------------------------------------------------------------------


class _Creature:
    def __init__(self, agent, graph_id="g1", name="alice", creature_id="cid"):
        self.agent = agent
        self.graph_id = graph_id
        self.name = name
        self.creature_id = creature_id
        self.is_privileged = False
        self.parent_creature_id = None

    def get_status(self):
        return {
            "creature_id": self.creature_id,
            "name": self.name,
            "is_running": True,
        }


class _Engine:
    def __init__(self, creatures=None, graphs=None, session_stores=None):
        self._creatures = creatures or {}
        self._graphs = graphs or {}
        self._session_stores = session_stores or {}
        self._environments = {}

    def get_creature(self, cid):
        if cid not in self._creatures:
            raise KeyError(cid)
        return self._creatures[cid]

    def get_graph(self, gid):
        if gid not in self._graphs:
            raise KeyError(gid)
        return self._graphs[gid]

    def list_graphs(self):
        return list(self._graphs.values())

    def list_output_wiring(self, cid):
        return []


class TestChatHistoryForBranches:
    def test_uses_agent_store_events_directly(self):
        """When the agent's own ``session_store`` yields events, they
        are returned and the engine-level fallback is not consulted."""

        class _Store:
            def get_resumable_events(self, name, live_job_ids=None):
                return [{"from": "agent_store"}]

        agent = SimpleNamespace(
            conversation_history=["msg"],
            session_store=_Store(),
            _processing_task=None,
            _direct_job_meta={},
        )
        eng = _Engine(creatures={"c1": _Creature(agent)})
        out = co.chat_history_for(eng, "c1")
        assert out["events"] == [{"from": "agent_store"}]
        assert out["messages"] == ["msg"]

    def test_falls_back_to_engine_session_store(self):
        """When the agent store yields nothing, ``chat_history_for``
        falls back to the engine's lifecycle-attached graph store."""

        class _EngineStore:
            def get_resumable_events(self, name, live_job_ids=None):
                return [{"from": "engine_store"}]

        agent = SimpleNamespace(
            conversation_history=[],
            session_store=None,
            _processing_task=None,
            _direct_job_meta={},
        )
        eng = _Engine(creatures={"c1": _Creature(agent)})
        eng._session_stores["g1"] = _EngineStore()
        out = co.chat_history_for(eng, "c1")
        assert out["events"] == [{"from": "engine_store"}]


# ---------------------------------------------------------------------------
# attach_policies_for — observer policy when channels exist
# ---------------------------------------------------------------------------


class TestAttachPoliciesObserver:
    def test_observer_policy_added_when_graph_has_channels(self):
        agent = SimpleNamespace(input_module=None, _input=None)
        creature = _Creature(agent, graph_id="g1")
        eng = _Engine(creatures={"c1": creature})
        eng._environments["g1"] = SimpleNamespace(
            shared_channels=SimpleNamespace(list_channels=lambda: ["chat"])
        )
        out = co.attach_policies_for(eng, "c1")
        assert "observer" in out

    def test_session_policy_io_when_privileged_present(self):
        priv = _Creature(SimpleNamespace())
        priv.is_privileged = True
        eng = _Engine(
            creatures={"c1": priv},
            graphs={
                "g1": GraphTopology(graph_id="g1", creature_ids={"c1"}, channels={})
            },
        )
        out = co.session_attach_policies_for(eng, "g1")
        assert out[0] == "io"


# ---------------------------------------------------------------------------
# _creatures_for_graph — root resolution precedence
# ---------------------------------------------------------------------------


class TestCreaturesForGraphRootResolution:
    def _engine_with(self, creatures):
        graph = GraphTopology(
            graph_id="g1",
            creature_ids=set(creatures.keys()),
            channels={},
        )
        eng = _Engine(creatures=creatures, graphs={"g1": graph})
        return eng, graph

    def test_root_by_creature_id_wins(self):
        """A privileged creature whose id is literally ``"root"`` is
        annotated ``is_root`` over other privileged peers."""
        root = _Creature(SimpleNamespace(), name="alpha", creature_id="root")
        root.is_privileged = True
        other = _Creature(SimpleNamespace(), name="beta", creature_id="zzz")
        other.is_privileged = True
        eng, graph = self._engine_with({"root": root, "zzz": other})
        out = co._creatures_for_graph(eng, graph)
        by_id = {c["creature_id"]: c for c in out}
        assert by_id["root"]["is_root"] is True
        assert by_id["zzz"]["is_root"] is False

    def test_root_by_name_when_no_id_match(self):
        """With no id-``root`` privileged creature, the one *named*
        ``root`` is chosen."""
        named_root = _Creature(SimpleNamespace(), name="root", creature_id="cid-a")
        named_root.is_privileged = True
        other = _Creature(SimpleNamespace(), name="beta", creature_id="cid-b")
        other.is_privileged = True
        eng, graph = self._engine_with({"cid-a": named_root, "cid-b": other})
        out = co._creatures_for_graph(eng, graph)
        by_id = {c["creature_id"]: c for c in out}
        assert by_id["cid-a"]["is_root"] is True

    def test_root_falls_back_to_lowest_sorted_privileged(self):
        """With neither id nor name ``root``, the lowest-sorted
        privileged id is annotated as root."""
        a = _Creature(SimpleNamespace(), name="alpha", creature_id="cid-a")
        a.is_privileged = True
        b = _Creature(SimpleNamespace(), name="beta", creature_id="cid-b")
        b.is_privileged = True
        eng, graph = self._engine_with({"cid-b": b, "cid-a": a})
        out = co._creatures_for_graph(eng, graph)
        by_id = {c["creature_id"]: c for c in out}
        assert by_id["cid-a"]["is_root"] is True
        assert by_id["cid-b"]["is_root"] is False

    def test_skips_missing_creature(self):
        """A creature_id in graph membership but absent from
        ``engine._creatures`` is skipped, not crashed on."""
        a = _Creature(SimpleNamespace(), name="alpha", creature_id="cid-a")
        graph = GraphTopology(
            graph_id="g1", creature_ids={"cid-a", "phantom"}, channels={}
        )
        eng = _Engine(creatures={"cid-a": a}, graphs={"g1": graph})
        out = co._creatures_for_graph(eng, graph)
        assert {c["creature_id"] for c in out} == {"cid-a"}


# ---------------------------------------------------------------------------
# _output_edges_for_graph — edge serialisation with a real edge
# ---------------------------------------------------------------------------


class TestOutputEdgesForGraph:
    def test_edges_annotated_with_from_and_target_resolution(self):
        """``_output_edges_for_graph`` stamps ``from`` / ``from_name`` /
        ``to_creature_id`` onto each output-wiring edge."""

        class _Engine2(_Engine):
            def list_output_wiring(self, cid):
                if cid == "cid-a":
                    return [{"edge_id": "e1", "to": "beta"}]
                return []

        graph = GraphTopology(
            graph_id="g1", creature_ids={"cid-a", "cid-b"}, channels={}
        )
        eng = _Engine2(graphs={"g1": graph})
        creatures = [
            {"creature_id": "cid-a", "name": "alpha"},
            {"creature_id": "cid-b", "name": "beta"},
        ]
        edges = co._output_edges_for_graph(eng, graph, creatures)
        assert len(edges) == 1
        edge = edges[0]
        assert edge["from"] == "cid-a"
        assert edge["from_name"] == "alpha"
        # "beta" name resolved back to its creature id.
        assert edge["to_creature_id"] == "cid-b"
        assert edge["graph_id"] == "g1"

    def test_creature_without_id_is_skipped(self):
        graph = GraphTopology(graph_id="g1", creature_ids=set(), channels={})
        eng = _Engine(graphs={"g1": graph})
        # A creature dict with neither creature_id nor agent_id.
        edges = co._output_edges_for_graph(eng, graph, [{"name": "x"}])
        assert edges == []


# ---------------------------------------------------------------------------
# _resolve_target_creature_id — root + topology-membership branches
# ---------------------------------------------------------------------------


class TestResolveTargetExtra:
    def test_root_target_resolves_to_is_root_creature(self):
        g = GraphTopology(graph_id="g", creature_ids={"c1"}, channels={})
        creatures = [{"creature_id": "c1", "name": "alpha", "is_root": True}]
        assert co._resolve_target_creature_id(g, creatures, "root") == "c1"

    def test_target_in_graph_membership_only(self):
        """A target that matches a topology creature_id but isn't in the
        serialised ``creatures`` list still resolves via membership."""
        g = GraphTopology(graph_id="g", creature_ids={"c1"}, channels={})
        assert co._resolve_target_creature_id(g, [], "c1") == "c1"


# ---------------------------------------------------------------------------
# agent_get_module_options — native_tool dispatch
# ---------------------------------------------------------------------------


class TestGetModuleOptionsNativeTool:
    def test_native_tool_lookup(self):
        class _Tool:
            is_provider_native = True
            description = "d"

            @staticmethod
            def provider_native_option_schema():
                return {"k": {"type": "string"}}

        class _Registry:
            def list_tools(self):
                return ["t1"]

            def get_tool(self, name):
                return _Tool()

        class _Helper:
            def get(self, name):
                return {"k": "v"}

        agent = SimpleNamespace(registry=_Registry(), native_tool_options=_Helper())
        out = co.agent_get_module_options(agent, "native_tool", "t1")
        assert out["type"] == "native_tool"
        assert out["options"] == {"k": "v"}

    def test_native_tool_unknown_raises(self):
        class _Registry:
            def list_tools(self):
                return []

            def get_tool(self, name):
                return None

        agent = SimpleNamespace(registry=_Registry(), native_tool_options=None)
        with pytest.raises(KeyError):
            co.agent_get_module_options(agent, "native_tool", "ghost")
