"""Unit tests for :mod:`kohakuterrarium.terrarium.tools_group_lifecycle`.

Tools are exercised by patching :func:`resolve_or_error` and
:func:`resolve_group_target` to return ready-made fakes — this keeps
the test focused on the lifecycle decisions (privilege checks, error
formatting, engine method dispatch) without spinning a full engine.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import kohakuterrarium.terrarium.tools_group_lifecycle as lifecycle_mod

# ── helpers ───────────────────────────────────────────────────


class _FakeCreature:
    def __init__(
        self,
        cid="cid-target",
        name="alice",
        graph_id="g1",
        is_privileged=False,
        is_running=False,
        parent_creature_id=None,
    ):
        self.creature_id = cid
        self.name = name
        self.graph_id = graph_id
        self.is_privileged = is_privileged
        self.is_running = is_running
        self.parent_creature_id = parent_creature_id
        self.agent = SimpleNamespace(executor=None)
        self._paused = False
        self._killed = False

    @property
    def paused(self):
        return self._paused

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False


class _FakeEngine:
    def __init__(self):
        self.add_creature = AsyncMock()
        self.remove_creature = AsyncMock()
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.connect = AsyncMock()
        self.emitted = []

    def _emit(self, event):
        self.emitted.append(event)


def _gctx(caller=None, engine=None):
    return SimpleNamespace(
        engine=engine or _FakeEngine(),
        caller=caller or _FakeCreature(cid="caller", name="root", is_privileged=True),
        graph=SimpleNamespace(graph_id="g1", creature_ids={"caller"}),
    )


def _patch_resolve(monkeypatch, gctx_value, err_value=None):
    monkeypatch.setattr(
        lifecycle_mod,
        "resolve_or_error",
        lambda ctx, **_: (gctx_value, err_value),
    )


def _patch_target(monkeypatch, target):
    monkeypatch.setattr(
        lifecycle_mod, "resolve_group_target", lambda gctx, name: target
    )


def _parse(result):
    return json.loads(result.output)


# ── group_add_node ────────────────────────────────────────────


class TestGroupAddNode:
    async def test_resolve_error_propagates(self, monkeypatch):
        sentinel_err = lifecycle_mod.err("bad")
        _patch_resolve(monkeypatch, None, err_value=sentinel_err)
        tool = lifecycle_mod.GroupAddNodeTool()
        r = await tool._execute({"config_path": "x"}, context=None)
        assert r.error == "bad"

    async def test_missing_config_path(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        tool = lifecycle_mod.GroupAddNodeTool()
        r = await tool._execute({"config_path": ""})
        assert "config_path is required" in r.error

    async def test_package_ref_passes_through_to_engine(self, monkeypatch):
        # ``@pkg/...`` refs are NOT pre-resolved by the tool — the
        # engine's config loader owns resolution (E1 chokepoint).  A
        # bad ref surfaces as the loader's typed error in the result.
        gctx = _gctx()
        gctx.engine.add_creature.side_effect = FileNotFoundError("no pkg")
        _patch_resolve(monkeypatch, gctx)
        tool = lifecycle_mod.GroupAddNodeTool()
        r = await tool._execute({"config_path": "@pkg/x"})
        assert "no pkg" in r.error
        # The unresolved ref reached the engine verbatim.
        assert gctx.engine.add_creature.call_args.args[0] == "@pkg/x"

    async def test_add_creature_failure(self, monkeypatch):
        gctx = _gctx()
        gctx.engine.add_creature.side_effect = RuntimeError("boom")
        _patch_resolve(monkeypatch, gctx)
        tool = lifecycle_mod.GroupAddNodeTool()
        r = await tool._execute({"config_path": "./c"})
        assert "failed to spawn" in r.error

    async def test_success_emits_event(self, monkeypatch):
        new_creature = _FakeCreature(
            cid="cid-new", name="alice", parent_creature_id="caller"
        )
        gctx = _gctx()
        gctx.engine.add_creature.return_value = new_creature
        _patch_resolve(monkeypatch, gctx)
        applied = {}
        monkeypatch.setattr(
            lifecycle_mod.group_hooks,
            "apply_creature_name",
            lambda c, n: applied.setdefault("name", n),
        )
        attached = {}
        monkeypatch.setattr(
            lifecycle_mod.group_hooks,
            "attach_session_store",
            lambda e, c, **kw: attached.update(kw),
        )
        tool = lifecycle_mod.GroupAddNodeTool()
        r = await tool._execute({"config_path": "./c", "name": "alice", "pwd": "/wd"})
        body = _parse(r)
        assert body["creature_id"] == "cid-new"
        assert body["parent_creature_id"] == "caller"
        assert gctx.engine.emitted  # emit fired


# ── group_remove_node ─────────────────────────────────────────


class TestGroupRemoveNode:
    async def test_target_missing(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, None)
        tool = lifecycle_mod.GroupRemoveNodeTool()
        r = await tool._execute({"creature_id": "ghost"})
        assert "not in your group" in r.error

    async def test_privileged_target_rejected(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, _FakeCreature(is_privileged=True))
        tool = lifecycle_mod.GroupRemoveNodeTool()
        r = await tool._execute({"creature_id": "p"})
        assert "cannot remove privileged" in r.error

    async def test_remove_failure(self, monkeypatch):
        gctx = _gctx()
        gctx.engine.remove_creature.side_effect = RuntimeError("nope")
        _patch_resolve(monkeypatch, gctx)
        _patch_target(monkeypatch, _FakeCreature())
        tool = lifecycle_mod.GroupRemoveNodeTool()
        r = await tool._execute({"creature_id": "c"})
        assert "remove failed" in r.error

    async def test_success_emits_when_parent(self, monkeypatch):
        gctx = _gctx()
        _patch_resolve(monkeypatch, gctx)
        _patch_target(monkeypatch, _FakeCreature(parent_creature_id="caller"))
        tool = lifecycle_mod.GroupRemoveNodeTool()
        r = await tool._execute({"creature_id": "c"})
        body = _parse(r)
        assert body["removed"] == "cid-target"
        assert gctx.engine.emitted

    async def test_success_no_parent_no_emit(self, monkeypatch):
        gctx = _gctx()
        _patch_resolve(monkeypatch, gctx)
        _patch_target(monkeypatch, _FakeCreature(parent_creature_id=None))
        tool = lifecycle_mod.GroupRemoveNodeTool()
        await tool._execute({"creature_id": "c"})
        assert not gctx.engine.emitted


# ── group_start_node / group_stop_node ────────────────────────


class TestGroupStartStop:
    async def test_start_missing_target(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, None)
        tool = lifecycle_mod.GroupStartNodeTool()
        r = await tool._execute({"creature_id": "ghost"})
        assert "not in your group" in r.error

    async def test_start_rejects_privileged(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, _FakeCreature(is_privileged=True))
        tool = lifecycle_mod.GroupStartNodeTool()
        r = await tool._execute({"creature_id": "p"})
        assert "cannot start/stop privileged" in r.error

    async def test_start_already_running(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, _FakeCreature(is_running=True))
        tool = lifecycle_mod.GroupStartNodeTool()
        r = await tool._execute({"creature_id": "c"})
        assert "already running" in r.error

    async def test_start_failure(self, monkeypatch):
        gctx = _gctx()
        gctx.engine.start.side_effect = RuntimeError("nope")
        _patch_resolve(monkeypatch, gctx)
        _patch_target(monkeypatch, _FakeCreature(is_running=False))
        tool = lifecycle_mod.GroupStartNodeTool()
        r = await tool._execute({"creature_id": "c"})
        assert "start failed" in r.error

    async def test_start_success(self, monkeypatch):
        gctx = _gctx()
        _patch_resolve(monkeypatch, gctx)
        _patch_target(monkeypatch, _FakeCreature(is_running=False))
        tool = lifecycle_mod.GroupStartNodeTool()
        r = await tool._execute({"creature_id": "c"})
        body = _parse(r)
        assert body["started"] == "cid-target"

    async def test_stop_missing(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, None)
        tool = lifecycle_mod.GroupStopNodeTool()
        r = await tool._execute({"creature_id": "ghost"})
        assert "not in your group" in r.error

    async def test_stop_rejects_privileged(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, _FakeCreature(is_privileged=True))
        tool = lifecycle_mod.GroupStopNodeTool()
        r = await tool._execute({"creature_id": "p"})
        assert "cannot start/stop" in r.error

    async def test_stop_not_running(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, _FakeCreature(is_running=False))
        tool = lifecycle_mod.GroupStopNodeTool()
        r = await tool._execute({"creature_id": "c"})
        assert "is not running" in r.error

    async def test_stop_failure(self, monkeypatch):
        gctx = _gctx()
        gctx.engine.stop.side_effect = RuntimeError("nope")
        _patch_resolve(monkeypatch, gctx)
        _patch_target(monkeypatch, _FakeCreature(is_running=True))
        tool = lifecycle_mod.GroupStopNodeTool()
        r = await tool._execute({"creature_id": "c"})
        assert "stop failed" in r.error

    async def test_stop_success(self, monkeypatch):
        gctx = _gctx()
        _patch_resolve(monkeypatch, gctx)
        _patch_target(monkeypatch, _FakeCreature(is_running=True))
        tool = lifecycle_mod.GroupStopNodeTool()
        r = await tool._execute({"creature_id": "c"})
        body = _parse(r)
        assert body["stopped"] == "cid-target"


# ── group_spawn_child ─────────────────────────────────────────


class TestGroupSpawnChild:
    def _child(self, **kw):
        child = _FakeCreature(**kw)
        child.agent = SimpleNamespace(executor=None, _process_event=AsyncMock())
        return child

    async def test_missing_config_ref(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        tool = lifecycle_mod.GroupSpawnChildTool()
        r = await tool._execute({"config_ref": ""})
        assert "config_ref is required" in r.error

    async def test_add_creature_failure(self, monkeypatch):
        gctx = _gctx()
        gctx.engine.add_creature.side_effect = RuntimeError("boom")
        _patch_resolve(monkeypatch, gctx)
        tool = lifecycle_mod.GroupSpawnChildTool()
        r = await tool._execute({"config_ref": "./c"})
        assert "failed to spawn" in r.error

    async def test_spawn_wires_default_channel_both_ways_and_delivers_task(
        self, monkeypatch
    ):
        gctx = _gctx()
        child = self._child(cid="cid-child", name="worker", parent_creature_id="caller")
        gctx.engine.add_creature.return_value = child
        _patch_resolve(monkeypatch, gctx)
        monkeypatch.setattr(
            lifecycle_mod.group_hooks, "attach_session_store", lambda e, c, **kw: None
        )
        tool = lifecycle_mod.GroupSpawnChildTool()
        r = await tool._execute(
            {"config_ref": "@pkg/creatures/worker", "task": "do the thing"}
        )
        body = _parse(r)
        assert body["creature_id"] == "cid-child"
        assert body["channel"] == "link-caller-cid-child"
        assert body["task_delivered"] is True
        # The default channel is wired BOTH ways: two connects on the same
        # channel (caller<->child).
        assert gctx.engine.connect.await_count == 2
        channels = {
            call.kwargs.get("channel") for call in gctx.engine.connect.await_args_list
        }
        assert channels == {"link-caller-cid-child"}
        # The task reached the child.
        await asyncio.sleep(0.01)
        child.agent._process_event.assert_awaited()

    async def test_spawn_without_task_delivers_nothing(self, monkeypatch):
        gctx = _gctx()
        child = self._child(cid="cid-child", name="worker")
        gctx.engine.add_creature.return_value = child
        _patch_resolve(monkeypatch, gctx)
        monkeypatch.setattr(
            lifecycle_mod.group_hooks, "attach_session_store", lambda e, c, **kw: None
        )
        tool = lifecycle_mod.GroupSpawnChildTool()
        r = await tool._execute({"config_ref": "./c"})
        body = _parse(r)
        assert body["task_delivered"] is False
        await asyncio.sleep(0.01)
        child.agent._process_event.assert_not_awaited()

    async def test_channel_wire_failure_surfaced(self, monkeypatch):
        gctx = _gctx()
        child = self._child(cid="cid-child", name="worker")
        gctx.engine.add_creature.return_value = child
        gctx.engine.connect.side_effect = RuntimeError("wire boom")
        _patch_resolve(monkeypatch, gctx)
        monkeypatch.setattr(
            lifecycle_mod.group_hooks, "attach_session_store", lambda e, c, **kw: None
        )
        tool = lifecycle_mod.GroupSpawnChildTool()
        r = await tool._execute({"config_ref": "./c"})
        assert "default channel wiring failed" in r.error


# ── group_pause_node / group_resume_node ──────────────────────


class TestGroupPauseResume:
    async def test_pause_missing_target(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, None)
        r = await lifecycle_mod.GroupPauseNodeTool()._execute({"creature_id": "ghost"})
        assert "not in your group" in r.error

    async def test_pause_rejects_privileged(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, _FakeCreature(is_privileged=True, is_running=True))
        r = await lifecycle_mod.GroupPauseNodeTool()._execute({"creature_id": "p"})
        assert "cannot pause privileged" in r.error

    async def test_pause_not_running(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, _FakeCreature(is_running=False))
        r = await lifecycle_mod.GroupPauseNodeTool()._execute({"creature_id": "c"})
        assert "is not running" in r.error

    async def test_pause_success(self, monkeypatch):
        target = _FakeCreature(is_running=True)
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, target)
        r = await lifecycle_mod.GroupPauseNodeTool()._execute({"creature_id": "c"})
        assert _parse(r)["paused"] == "cid-target"
        assert target.paused is True

    async def test_pause_already_paused(self, monkeypatch):
        target = _FakeCreature(is_running=True)
        target.pause()
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, target)
        r = await lifecycle_mod.GroupPauseNodeTool()._execute({"creature_id": "c"})
        assert "already paused" in r.error

    async def test_resume_success(self, monkeypatch):
        target = _FakeCreature(is_running=True)
        target.pause()
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, target)
        r = await lifecycle_mod.GroupResumeNodeTool()._execute({"creature_id": "c"})
        assert _parse(r)["resumed"] == "cid-target"
        assert target.paused is False

    async def test_resume_not_paused(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, _FakeCreature(is_running=True))
        r = await lifecycle_mod.GroupResumeNodeTool()._execute({"creature_id": "c"})
        assert "is not paused" in r.error


# ── group_kill_node ───────────────────────────────────────────


class TestGroupKill:
    async def test_kill_rejects_privileged(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        _patch_target(monkeypatch, _FakeCreature(is_privileged=True, is_running=True))
        r = await lifecycle_mod.GroupKillNodeTool()._execute({"creature_id": "p"})
        assert "cannot kill privileged" in r.error

    async def test_kill_force_stops_and_marks_no_hard_kill(self, monkeypatch):
        gctx = _gctx()
        target = _FakeCreature(is_running=True)
        _patch_resolve(monkeypatch, gctx)
        _patch_target(monkeypatch, target)
        r = await lifecycle_mod.GroupKillNodeTool()._execute({"creature_id": "c"})
        body = _parse(r)
        assert body["killed"] == "cid-target"
        # Honest about the shared-process limitation.
        assert body["hard_kill_supported"] is False
        # Force-stop went through engine.stop; killed marker set.
        gctx.engine.stop.assert_awaited_once_with("cid-target")
        assert target._killed is True

    async def test_kill_stop_failure(self, monkeypatch):
        gctx = _gctx()
        gctx.engine.stop.side_effect = RuntimeError("nope")
        _patch_resolve(monkeypatch, gctx)
        _patch_target(monkeypatch, _FakeCreature(is_running=True))
        r = await lifecycle_mod.GroupKillNodeTool()._execute({"creature_id": "c"})
        assert "kill failed" in r.error

    async def test_kill_not_running_rejected(self, monkeypatch):
        # Parity with group_stop_node: killing an already-stopped worker is a
        # clean error, not a no-op force-stop.
        gctx = _gctx()
        _patch_resolve(monkeypatch, gctx)
        _patch_target(monkeypatch, _FakeCreature(is_running=False))
        r = await lifecycle_mod.GroupKillNodeTool()._execute({"creature_id": "c"})
        assert "is not running" in r.error
        gctx.engine.stop.assert_not_awaited()


# ── group_status prompt contribution (UXI-10) ─────────────────


class TestStatusPromptContribution:
    def test_teaches_spawn_and_distinct_lifecycle_verbs(self):
        from kohakuterrarium.terrarium.tools_group_status import GroupStatusTool

        prompt = GroupStatusTool().prompt_contribution()
        # Mental model: graph creature as an unbounded sub-agent.
        assert "unbounded sub-agent" in prompt
        # The one-call shortcut is taught.
        assert "group_spawn_child" in prompt
        # Distinct lifecycle verbs, and the false "stop == pause" claim is gone.
        assert "group_pause_node" in prompt
        assert "group_kill_node" in prompt
        assert "pauses it without removing" not in prompt
        # Prompt drift fixed: no stale from_id/to_id; direction is send|listen only.
        assert "from_id" not in prompt and "to_id" not in prompt


# ── _caller_pwd ──────────────────────────────────────────────


class TestCallerPwd:
    def test_no_executor(self):
        gctx = _gctx()
        assert lifecycle_mod._caller_pwd(gctx) == ""

    def test_with_executor(self):
        c = _FakeCreature()
        c.agent = SimpleNamespace(executor=SimpleNamespace(_working_dir="/wd"))
        gctx = _gctx(caller=c)
        assert lifecycle_mod._caller_pwd(gctx) == "/wd"

    def test_with_executor_no_wd(self):
        c = _FakeCreature()
        c.agent = SimpleNamespace(executor=SimpleNamespace(_working_dir=None))
        gctx = _gctx(caller=c)
        assert lifecycle_mod._caller_pwd(gctx) == ""


# ── tool schema metadata ──────────────────────────────────────


@pytest.mark.parametrize(
    "tool_cls",
    [
        lifecycle_mod.GroupAddNodeTool,
        lifecycle_mod.GroupSpawnChildTool,
        lifecycle_mod.GroupRemoveNodeTool,
        lifecycle_mod.GroupStartNodeTool,
        lifecycle_mod.GroupStopNodeTool,
        lifecycle_mod.GroupPauseNodeTool,
        lifecycle_mod.GroupResumeNodeTool,
        lifecycle_mod.GroupKillNodeTool,
    ],
)
def test_tool_metadata(tool_cls):
    t = tool_cls()
    assert t.tool_name.startswith("group_")
    assert t.description
    assert t.execution_mode.name == "DIRECT"
    assert "properties" in t.get_parameters_schema()
