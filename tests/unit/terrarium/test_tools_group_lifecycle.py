"""Unit tests for :mod:`kohakuterrarium.terrarium.tools_group_lifecycle`.

Tools are exercised by patching :func:`resolve_or_error` and
:func:`resolve_group_target` to return ready-made fakes — this keeps
the test focused on the lifecycle decisions (privilege checks, error
formatting, engine method dispatch) without spinning a full engine.
"""

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


class _FakeEngine:
    def __init__(self):
        self.add_creature = AsyncMock()
        self.remove_creature = AsyncMock()
        self.start = AsyncMock()
        self.stop = AsyncMock()
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

    async def test_package_ref_resolution_failure(self, monkeypatch):
        _patch_resolve(monkeypatch, _gctx())
        monkeypatch.setattr(lifecycle_mod, "is_package_ref", lambda p: True)

        def boom(p):
            raise FileNotFoundError("no pkg")

        monkeypatch.setattr(lifecycle_mod, "resolve_package_path", boom)
        tool = lifecycle_mod.GroupAddNodeTool()
        r = await tool._execute({"config_path": "@pkg/x"})
        assert "no pkg" in r.error

    async def test_add_creature_failure(self, monkeypatch):
        gctx = _gctx()
        gctx.engine.add_creature.side_effect = RuntimeError("boom")
        _patch_resolve(monkeypatch, gctx)
        monkeypatch.setattr(lifecycle_mod, "is_package_ref", lambda p: False)
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
        monkeypatch.setattr(lifecycle_mod, "is_package_ref", lambda p: False)
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
        lifecycle_mod.GroupRemoveNodeTool,
        lifecycle_mod.GroupStartNodeTool,
        lifecycle_mod.GroupStopNodeTool,
    ],
)
def test_tool_metadata(tool_cls):
    t = tool_cls()
    assert t.tool_name.startswith("group_")
    assert t.description
    assert t.execution_mode.name == "DIRECT"
    assert "properties" in t.get_parameters_schema()
