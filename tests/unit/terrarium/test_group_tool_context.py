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
        self._topology = SimpleNamespace(graphs=graphs or {})


class _Env:
    def __init__(self, lookup=None):
        self._lookup = lookup or {}

    def get(self, key):
        return self._lookup.get(key)


def _ctx(env=None, agent_name="caller"):
    from pathlib import Path

    return ToolContext(
        agent_name=agent_name,
        session=None,
        working_dir=Path("."),
        environment=env,
    )


# ── find_creature ──────────────────────────────────────────────


class TestFindCreature:
    def test_by_id(self):
        c = _Creature("cid-1")
        eng = _Engine({"cid-1": c})
        out = gtc.find_creature(eng, "cid-1")
        assert out is c

    def test_by_name(self):
        c = _Creature("cid-1", name="alice")
        eng = _Engine({"cid-1": c})
        out = gtc.find_creature(eng, "alice")
        assert out is c

    def test_by_config_name(self):
        c = _Creature("cid-1", name="alice")
        c.agent.config.name = "alpha"
        c.name = "other"
        eng = _Engine({"cid-1": c})
        out = gtc.find_creature(eng, "alpha")
        assert out is c

    def test_unknown(self):
        eng = _Engine()
        assert gtc.find_creature(eng, "ghost") is None


# ── resolve_group_context ──────────────────────────────────────


def _engine_with_graph(privileged=True):
    g = SimpleNamespace(creature_ids={"caller"}, channels=set())
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
        ctx = _ctx(env=env, agent_name="ghost")
        with pytest.raises(gtc.GroupToolError, match="not a creature"):
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
        eng = _Engine({"caller": c}, graphs={})  # no g-orphan
        env = _Env({TERRARIUM_ENGINE_KEY: eng})
        with pytest.raises(gtc.GroupToolError, match="not present in topology"):
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

    def test_includes_spawned_children(self):
        a = _Creature("a", graph_id="g1")
        child = _Creature("child", graph_id="g-other", parent_creature_id="a")
        graph = SimpleNamespace(creature_ids={"a"})
        eng = _Engine({"a": a, "child": child})
        gctx = gtc.GroupContext(engine=eng, caller=a, graph=graph)
        group = gtc.compute_group(gctx)
        assert "child" in group


class TestResolveGroupTarget:
    def test_by_id(self):
        a = _Creature("a", graph_id="g1")
        b = _Creature("b", graph_id="g1")
        graph = SimpleNamespace(creature_ids={"a", "b"})
        eng = _Engine({"a": a, "b": b})
        gctx = gtc.GroupContext(engine=eng, caller=a, graph=graph)
        assert gtc.resolve_group_target(gctx, "b") is b

    def test_by_name(self):
        a = _Creature("cid-a", name="alice", graph_id="g1")
        graph = SimpleNamespace(creature_ids={"cid-a"})
        eng = _Engine({"cid-a": a})
        gctx = gtc.GroupContext(engine=eng, caller=a, graph=graph)
        assert gtc.resolve_group_target(gctx, "alice") is a

    def test_by_config_name(self):
        a = _Creature("cid-a", name="alice", graph_id="g1")
        a.agent.config.name = "alpha"
        a.name = "other"
        graph = SimpleNamespace(creature_ids={"cid-a"})
        eng = _Engine({"cid-a": a})
        gctx = gtc.GroupContext(engine=eng, caller=a, graph=graph)
        assert gtc.resolve_group_target(gctx, "alpha") is a

    def test_unknown_returns_none(self):
        a = _Creature("a", graph_id="g1")
        graph = SimpleNamespace(creature_ids={"a"})
        eng = _Engine({"a": a})
        gctx = gtc.GroupContext(engine=eng, caller=a, graph=graph)
        assert gtc.resolve_group_target(gctx, "ghost") is None


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
