"""Unit tests for :mod:`kohakuterrarium.terrarium.group_tool_context`."""

import weakref
from types import SimpleNamespace

import pytest

from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium import group_tool_context as gtc
from kohakuterrarium.terrarium.channels import TERRARIUM_ENGINE_KEY

# ── fakes ──────────────────────────────────────────────────────


class _Creature:
    def __init__(
        self,
        cid,
        *,
        name=None,
        graph_id="g1",
        is_privileged=True,
        parent_creature_id=None,
    ):
        self.creature_id = cid
        self.name = name or cid
        self.graph_id = graph_id
        self.is_privileged = is_privileged
        self.parent_creature_id = parent_creature_id
        self.agent = SimpleNamespace(config=SimpleNamespace(name=name or cid))


class _Engine:
    def __init__(self, creatures=None, graphs=None):
        self._creatures = creatures or {}
        graph_map = graphs or {}
        creature_to_graph = {
            creature_id: graph_id
            for graph_id, graph in graph_map.items()
            for creature_id in graph.creature_ids
        }
        self._topology = SimpleNamespace(
            graphs=graph_map, creature_to_graph=creature_to_graph
        )


class _Env:
    def __init__(self, lookup=None):
        self._lookup = lookup or {}

    def get(self, key):
        return self._lookup.get(key)


def _ctx(env=None, agent_name="caller", creature_id="caller"):
    from pathlib import Path

    return ToolContext(
        agent_name=agent_name,
        session=None,
        working_dir=Path("."),
        creature_id=creature_id,
        environment=env,
    )


# ── resolve_group_context ──────────────────────────────────────


def _engine_with_graph(privileged=True):
    g = SimpleNamespace(graph_id="g1", creature_ids={"caller"}, channels=set())
    c = _Creature("caller", is_privileged=privileged)
    return _Engine({"caller": c}, graphs={"g1": g})


class TestResolveGroupContext:
    def test_no_ctx_raises(self):
        with pytest.raises(gtc.GroupToolError, match="tool context"):
            gtc.resolve_group_context(None)

    def test_no_environment_raises(self):
        with pytest.raises(gtc.GroupToolError, match="tool context"):
            gtc.resolve_group_context(_ctx(env=None))

    def test_no_engine_in_env_raises(self):
        with pytest.raises(gtc.GroupToolError, match="live terrarium engine"):
            gtc.resolve_group_context(_ctx(env=_Env(lookup={})))

    def test_weakref_engine(self):
        class _WR:
            pass

        eng = _engine_with_graph()
        # Build a fake weakref via a class that supports it.
        # Bypass weakref by passing the engine directly (the helper
        # handles both weakref.ref and live objects).
        env = _Env({TERRARIUM_ENGINE_KEY: eng})
        ctx = _ctx(env=env)
        out = gtc.resolve_group_context(ctx)
        assert out.caller.creature_id == "caller"

    def test_unknown_caller_raises(self):
        eng = _engine_with_graph()
        env = _Env({TERRARIUM_ENGINE_KEY: eng})
        ctx = _ctx(env=env, agent_name="caller", creature_id="ghost")
        with pytest.raises(gtc.GroupToolError, match="known runtime creature"):
            gtc.resolve_group_context(ctx)

    def test_caller_name_never_overrides_runtime_id(self):
        eng = _engine_with_graph()
        env = _Env({TERRARIUM_ENGINE_KEY: eng})
        ctx = _ctx(env=env, agent_name="caller", creature_id="ghost")
        with pytest.raises(gtc.GroupToolError, match="ghost"):
            gtc.resolve_group_context(ctx)

    def test_legacy_context_without_creature_id_is_explicitly_rejected(self):
        eng = _engine_with_graph()
        env = _Env({TERRARIUM_ENGINE_KEY: eng})
        ctx = SimpleNamespace(agent_name="caller", environment=env)
        with pytest.raises(gtc.GroupToolError, match="ToolContext.creature_id"):
            gtc.resolve_group_context(ctx)

    def test_require_privileged_rejects_non_privileged(self):
        eng = _engine_with_graph(privileged=False)
        env = _Env({TERRARIUM_ENGINE_KEY: eng})
        ctx = _ctx(env=env)
        with pytest.raises(gtc.GroupToolError, match="privileged"):
            gtc.resolve_group_context(ctx)

    def test_require_privileged_false_accepts_anyone(self):
        eng = _engine_with_graph(privileged=False)
        env = _Env({TERRARIUM_ENGINE_KEY: eng})
        ctx = _ctx(env=env)
        out = gtc.resolve_group_context(ctx, require_privileged=False)
        assert out.caller.creature_id == "caller"

    def test_missing_graph_raises(self):
        c = _Creature("caller", graph_id="g-orphan")
        eng = _Engine({"caller": c}, graphs={})
        env = _Env({TERRARIUM_ENGINE_KEY: eng})
        with pytest.raises(gtc.GroupToolError, match="not assigned to a graph"):
            gtc.resolve_group_context(_ctx(env=env))

    def test_dead_weakref(self):
        class _W:
            pass

        eng = _W()
        ref = weakref.ref(eng)
        env = _Env({TERRARIUM_ENGINE_KEY: ref})
        del eng
        with pytest.raises(gtc.GroupToolError, match="live terrarium engine"):
            gtc.resolve_group_context(_ctx(env=env))


# ── compute_group / resolve_group_target ──────────────────────


class TestComputeGroup:
    def test_includes_graph_members(self):
        a = _Creature("a", graph_id="g1")
        b = _Creature("b", graph_id="g1")
        graph = SimpleNamespace(creature_ids={"a", "b"})
        eng = _Engine({"a": a, "b": b})
        gctx = gtc.GroupContext(engine=eng, caller=a, graph=graph)
        group = gtc.compute_group(gctx)
        assert set(group.keys()) == {"a", "b"}

    def test_excludes_spawned_children_outside_graph(self):
        a = _Creature("a", graph_id="g1")
        child = _Creature("child", graph_id="g-other", parent_creature_id="a")
        graph = SimpleNamespace(graph_id="g1", creature_ids={"a"})
        eng = _Engine({"a": a, "child": child}, graphs={"g1": graph})
        gctx = gtc.GroupContext(engine=eng, caller=a, graph=graph)
        group = gtc.compute_group(gctx)
        assert "child" not in group


class TestResolveGroupTarget:
    @staticmethod
    def _context(*creatures):
        graph = SimpleNamespace(
            graph_id="g1", creature_ids={creature.creature_id for creature in creatures}
        )
        engine = _Engine(
            {creature.creature_id: creature for creature in creatures},
            graphs={"g1": graph},
        )
        return gtc.GroupContext(engine=engine, caller=creatures[0], graph=graph)

    def test_by_id(self):
        a = _Creature("a")
        b = _Creature("b")
        assert gtc.resolve_group_target(self._context(a, b), "b") is b

    def test_exact_self_send_uses_runtime_id(self):
        a = _Creature("caller", name="shared")
        b = _Creature("other", name="shared")
        assert gtc.resolve_group_target(self._context(a, b), "caller") is a

    def test_by_config_name(self):
        a = _Creature("cid-a", name="alice")
        a.agent.config.name = "alpha"
        a.name = "other"
        assert gtc.resolve_group_target(self._context(a), "alpha") is a

    def test_duplicate_display_name_is_ambiguous(self):
        a = _Creature("a", name="same")
        b = _Creature("b", name="same")
        with pytest.raises(gtc.GroupToolError, match="ambiguous"):
            gtc.resolve_group_target(self._context(a, b), "same")

    def test_duplicate_config_name_is_ambiguous(self):
        a = _Creature("a")
        b = _Creature("b")
        a.agent.config.name = b.agent.config.name = "same-config"
        with pytest.raises(gtc.GroupToolError, match="ambiguous"):
            gtc.resolve_group_target(self._context(a, b), "same-config")

    def test_cross_graph_exact_id_is_rejected(self):
        a = _Creature("a")
        other = _Creature("other", graph_id="g2")
        graph = SimpleNamespace(graph_id="g1", creature_ids={"a"})
        engine = _Engine({"a": a, "other": other}, graphs={"g1": graph})
        gctx = gtc.GroupContext(engine=engine, caller=a, graph=graph)
        with pytest.raises(gtc.GroupToolError, match="not a creature"):
            gtc.resolve_group_target(gctx, "other")

    def test_unknown_fails_closed(self):
        a = _Creature("a")
        with pytest.raises(gtc.GroupToolError, match="not a creature"):
            gtc.resolve_group_target(self._context(a), "ghost")


# ── CF-7: cross-cluster awareness ──────────────────────────────


class TestEngineIsInCluster:
    """``engine_is_in_cluster`` is the cheapest in-engine signal that
    this engine is a Lab cluster member.  Worker adapters stash
    ``_broadcast_adapter`` / ``_output_wire_adapter`` on the engine at
    boot; standalone engines have neither.  Behavior assert each
    branch so the heuristic doesn't silently flip later."""

    def test_standalone_engine_returns_false(self):
        eng = _Engine()
        assert gtc.engine_is_in_cluster(eng) is False

    def test_with_broadcast_adapter_returns_true(self):
        eng = _Engine()
        eng._broadcast_adapter = object()
        assert gtc.engine_is_in_cluster(eng) is True

    def test_with_output_wire_adapter_returns_true(self):
        eng = _Engine()
        eng._output_wire_adapter = object()
        assert gtc.engine_is_in_cluster(eng) is True


class TestCrossClusterTargetError:
    """CF-7: the cluster-aware miss message must mention 'cross-cluster'
    + 'CF-7' so the LLM/user can tell a typo from a cross-worker
    miss.  In standalone the standard "not in your group" wording
    survives so the standalone test suite doesn't regress."""

    def test_standalone_falls_through_to_plain_miss(self):
        eng = _Engine()
        msg = gtc.cross_cluster_target_error(eng, "bravo")
        assert "cross-cluster" not in msg
        assert "bravo" in msg
        assert "not in your group" in msg

    def test_cluster_engine_surfaces_cf7_tag(self):
        eng = _Engine()
        eng._broadcast_adapter = object()
        msg = gtc.cross_cluster_target_error(eng, "bravo")
        assert "cross-cluster" in msg
        assert "CF-7" in msg
        assert "bravo" in msg
