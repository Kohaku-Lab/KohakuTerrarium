"""Unit tests for :mod:`kohakuterrarium.studio.sessions.creature_state`."""

import os
from datetime import datetime
from types import SimpleNamespace

import pytest

from kohakuterrarium.studio.sessions import creature_state as state_mod

# ── _redacted_env ──────────────────────────────────────────────


class TestRedactedEnv:
    def test_redacts_secrets(self, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin")
        monkeypatch.setenv("OPENAI_KEY", "sk-abc")
        monkeypatch.setenv("MY_SECRET", "supersecret")
        monkeypatch.setenv("USER_PASSWORD", "x")
        out = state_mod._redacted_env()
        assert "PATH" in out
        assert "OPENAI_KEY" not in out
        assert "MY_SECRET" not in out
        assert "USER_PASSWORD" not in out


# ── helpers ────────────────────────────────────────────────────


class _FakeScratchpad:
    def __init__(self, initial=None):
        self._d = dict(initial or {})
        self.deleted: list[str] = []

    def to_dict(self):
        return dict(self._d)

    def set(self, k, v):
        self._d[k] = v

    def delete(self, k):
        self.deleted.append(k)
        self._d.pop(k, None)


class _FakeTriggerInfo:
    def __init__(self, tid):
        self.trigger_id = tid
        self.trigger_type = "channel"
        self.running = True
        self.created_at = datetime.now()


class _FakeTriggerManager:
    def __init__(self, triggers=None):
        self._triggers = triggers or []

    def list(self):
        return list(self._triggers)


class _FakeNativeTool:
    is_provider_native = True
    description = "img gen"

    @classmethod
    def provider_native_option_schema(cls):
        return {"size": {"type": "string"}}


class _FakeNonNativeTool:
    is_provider_native = False
    description = "ordinary"


class _FakeRegistry:
    def __init__(self, tools=None):
        self._tools = tools or {}

    def list_tools(self):
        return sorted(self._tools.keys())

    def get_tool(self, name):
        return self._tools.get(name)


class _FakeHelper:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, name):
        return self._values.get(name, {})

    def list(self):
        return dict(self._values)

    def set(self, tool, values):
        self._values[tool] = values
        return values


class _FakeWorkspace:
    def __init__(self, path="/cwd"):
        self._path = path

    def get(self):
        return self._path

    def set(self, p):
        self._path = p
        return p


def _agent(
    *,
    scratchpad=None,
    triggers=None,
    workspace=None,
    helper=None,
    registry=None,
    system_prompt="be helpful",
    executor=None,
):
    return SimpleNamespace(
        scratchpad=scratchpad or _FakeScratchpad(),
        trigger_manager=_FakeTriggerManager(triggers or []),
        workspace=workspace,
        native_tool_options=helper,
        registry=registry or _FakeRegistry(),
        get_system_prompt=lambda: system_prompt,
        executor=executor,
    )


def _creature(agent):
    return SimpleNamespace(agent=agent)


# Use lifecycle.find_creature monkeypatching for routing.


def _install_find(monkeypatch, agent):
    monkeypatch.setattr(
        state_mod, "find_creature", lambda eng, sid, cid: _creature(agent)
    )


def _engine():
    return SimpleNamespace()


# ── scratchpad ─────────────────────────────────────────────────


class TestScratchpad:
    def test_get(self, monkeypatch):
        agent = _agent(scratchpad=_FakeScratchpad({"k": "v"}))
        _install_find(monkeypatch, agent)
        out = state_mod.get_scratchpad(_engine(), "g", "c")
        assert out == {"k": "v"}

    def test_patch_set(self, monkeypatch):
        agent = _agent(scratchpad=_FakeScratchpad())
        _install_find(monkeypatch, agent)
        out = state_mod.patch_scratchpad(_engine(), "g", "c", {"k": "new"})
        assert out == {"k": "new"}

    def test_patch_delete(self, monkeypatch):
        sp = _FakeScratchpad({"k": "v"})
        agent = _agent(scratchpad=sp)
        _install_find(monkeypatch, agent)
        state_mod.patch_scratchpad(_engine(), "g", "c", {"k": None})
        assert sp.deleted == ["k"]

    def test_reserved_key_raises(self, monkeypatch):
        agent = _agent()
        _install_find(monkeypatch, agent)
        # Reserved keys start with __; pick a real reserved per is_reserved_scratchpad_key.
        # The function returns True for keys starting with ``__``.
        with pytest.raises(ValueError, match="Reserved"):
            state_mod.patch_scratchpad(_engine(), "g", "c", {"__internal__": "x"})


# ── triggers ───────────────────────────────────────────────────


class TestTriggers:
    def test_list_with_triggers(self, monkeypatch):
        triggers = [_FakeTriggerInfo("t1"), _FakeTriggerInfo("t2")]
        agent = _agent(triggers=triggers)
        _install_find(monkeypatch, agent)
        out = state_mod.list_triggers(_engine(), "g", "c")
        assert {t["trigger_id"] for t in out} == {"t1", "t2"}

    def test_list_no_manager(self, monkeypatch):
        agent = _agent()
        agent.trigger_manager = None
        _install_find(monkeypatch, agent)
        out = state_mod.list_triggers(_engine(), "g", "c")
        assert out == []


# ── env / system_prompt ───────────────────────────────────────


class TestEnvAndPrompt:
    def test_get_env(self, monkeypatch):
        # The working directory is owned by the agent's executor (the
        # real Agent shape) — get_env must report it as ``pwd``, the
        # same value get_working_dir resolves.
        agent = _agent(executor=SimpleNamespace(_working_dir="/tmp"))
        _install_find(monkeypatch, agent)
        out = state_mod.get_env(_engine(), "g", "c")
        assert out["pwd"] == "/tmp"
        # env is the redacted os.environ snapshot.
        assert out["env"] == state_mod._redacted_env()

    def test_get_env_default_pwd(self, monkeypatch):
        agent = _agent()
        # No workspace + no executor working dir → falls back to os.getcwd().
        _install_find(monkeypatch, agent)
        out = state_mod.get_env(_engine(), "g", "c")
        assert out["pwd"] == os.getcwd()

    def test_get_system_prompt(self, monkeypatch):
        agent = _agent(system_prompt="sys")
        _install_find(monkeypatch, agent)
        out = state_mod.get_system_prompt(_engine(), "g", "c")
        assert out == {"text": "sys"}


# ── working dir ────────────────────────────────────────────────


class TestWorkingDir:
    def test_get_with_workspace(self, monkeypatch):
        agent = _agent(workspace=_FakeWorkspace("/cwd"))
        _install_find(monkeypatch, agent)
        out = state_mod.get_working_dir(_engine(), "g", "c")
        assert out == "/cwd"

    def test_get_without_workspace(self, monkeypatch):
        executor = SimpleNamespace(_working_dir="/exec-cwd")
        agent = _agent(executor=executor)
        _install_find(monkeypatch, agent)
        out = state_mod.get_working_dir(_engine(), "g", "c")
        assert out == "/exec-cwd"

    def test_set_with_workspace(self, monkeypatch):
        ws = _FakeWorkspace()
        agent = _agent(workspace=ws)
        _install_find(monkeypatch, agent)
        out = state_mod.set_working_dir(_engine(), "g", "c", "/new")
        assert out == "/new"
        assert ws.get() == "/new"

    def test_set_without_workspace_raises(self, monkeypatch):
        agent = _agent()
        _install_find(monkeypatch, agent)
        with pytest.raises(RuntimeError, match="no workspace"):
            state_mod.set_working_dir(_engine(), "g", "c", "/x")


# ── native tool options ───────────────────────────────────────


class TestNativeToolOptions:
    def test_inventory_filters_non_native(self, monkeypatch):
        registry = _FakeRegistry(
            tools={
                "image_gen": _FakeNativeTool(),
                "bash": _FakeNonNativeTool(),
            }
        )
        helper = _FakeHelper({"image_gen": {"size": "256"}})
        agent = _agent(registry=registry, helper=helper)
        _install_find(monkeypatch, agent)
        out = state_mod.native_tool_inventory(_engine(), "g", "c")
        names = [e["name"] for e in out]
        assert "image_gen" in names
        assert "bash" not in names
        img = next(e for e in out if e["name"] == "image_gen")
        assert img["option_schema"] == {"size": {"type": "string"}}

    def test_inventory_handles_schema_error(self, monkeypatch):
        class _BadTool:
            is_provider_native = True
            description = ""

            @classmethod
            def provider_native_option_schema(cls):
                raise RuntimeError("boom")

        registry = _FakeRegistry({"bad": _BadTool()})
        agent = _agent(registry=registry, helper=_FakeHelper())
        _install_find(monkeypatch, agent)
        out = state_mod.native_tool_inventory(_engine(), "g", "c")
        # No raise; schema falls back to {}.
        assert out[0]["option_schema"] == {}

    def test_get_options(self, monkeypatch):
        helper = _FakeHelper({"image_gen": {"size": "256"}})
        agent = _agent(helper=helper)
        _install_find(monkeypatch, agent)
        out = state_mod.get_native_tool_options(_engine(), "g", "c")
        assert out == {"image_gen": {"size": "256"}}

    def test_get_options_no_helper(self, monkeypatch):
        agent = _agent()
        _install_find(monkeypatch, agent)
        out = state_mod.get_native_tool_options(_engine(), "g", "c")
        assert out == {}

    def test_set_options(self, monkeypatch):
        helper = _FakeHelper()
        agent = _agent(helper=helper)
        _install_find(monkeypatch, agent)
        out = state_mod.set_native_tool_options(
            _engine(), "g", "c", "image_gen", {"size": "1024"}
        )
        assert out == {"size": "1024"}
        assert helper._values["image_gen"] == {"size": "1024"}

    def test_set_options_no_helper(self, monkeypatch):
        agent = _agent()
        _install_find(monkeypatch, agent)
        with pytest.raises(ValueError, match="no native_tool_options"):
            state_mod.set_native_tool_options(_engine(), "g", "c", "t", {})
