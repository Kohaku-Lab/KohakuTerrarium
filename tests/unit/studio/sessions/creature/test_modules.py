"""Unit tests for :mod:`kohakuterrarium.studio.sessions.creature.modules`."""

from types import SimpleNamespace

import pytest

import kohakuterrarium as kt
from kohakuterrarium.core.config.types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.studio.sessions.creature import modules as mod
from kohakuterrarium.testing.llm import ScriptedLLM

# ── helpers ──────────────────────────────────────────────────────


class _FakeService:
    """LocalTerrariumService stand-in: ``as_engine`` reads `engine`."""

    def __init__(self):
        self.engine = object()


def _patch_adapter_inventory(
    monkeypatch, plugin_rows=None, native_rows=None, tool_rows=None
):
    monkeypatch.setattr(
        mod.creature_plugins,
        "plugin_inventory",
        lambda e, sid, cid: plugin_rows or [],
    )
    monkeypatch.setattr(
        mod.creature_state,
        "native_tool_inventory",
        lambda e, sid, cid: native_rows or [],
    )
    monkeypatch.setattr(
        mod.creature_state,
        "tool_inventory",
        lambda e, sid, cid: tool_rows or [],
    )


class _Probe(BaseTool):
    def __init__(self, name, native=False):
        super().__init__()
        self._name = name
        self.is_provider_native = native

    @property
    def tool_name(self):
        return self._name

    @property
    def description(self):
        return self._name

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output="ran")


async def _tool_agent(tmp_path):
    cfg = AgentConfig(
        name="studio_agent",
        system_prompt="Test.",
        include_hints_in_prompt=False,
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
    )
    agent = await kt.Agent.build(cfg, llm=ScriptedLLM(["ok"]))
    agent.add_tool(_Probe("probe"))
    agent.add_tool(_Probe("hosted", native=True))
    return agent


def _install_creature(monkeypatch, agent):
    monkeypatch.setattr(
        mod, "find_creature", lambda engine, sid, cid: SimpleNamespace(agent=agent)
    )


# ── supported_types ─────────────────────────────────────────────


class TestSupportedTypes:
    def test_basic(self):
        assert mod.supported_types() == ["plugin", "native_tool", "tool"]


# ── _adapter ────────────────────────────────────────────────────


class TestAdapter:
    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown module type"):
            mod._adapter("ghost", "inventory")

    def test_unsupported_op_raises(self):
        # ``inventory`` exists for plugin; pretend the dispatch table
        # entry is missing ``toggle``. We just use a real plugin entry
        # but the dispatcher returns a defined ``toggle`` for plugin —
        # check ``native_tool`` with an unknown op.
        with pytest.raises(ValueError, match="does not support"):
            mod._adapter("plugin", "ghost_op")


# ── list_modules ────────────────────────────────────────────────


class TestListModules:
    def test_combines_types(self, monkeypatch):
        _patch_adapter_inventory(
            monkeypatch,
            plugin_rows=[{"name": "p1", "description": "pd", "enabled": False}],
            native_rows=[
                {
                    "name": "nt1",
                    "description": "nd",
                    "option_schema": {"k": 1},
                    "enabled": False,
                    "origin": {"kind": "builtin", "name": "", "label": "builtin"},
                }
            ],
            tool_rows=[
                {
                    "name": "web_search",
                    "description": "search",
                    "values": {"backend": "duckduckgo"},
                    "enabled": True,
                    "origin": {
                        "kind": "package",
                        "name": "kt-biome",
                        "label": "package:kt-biome",
                    },
                }
            ],
        )
        svc = _FakeService()
        out = mod.list_modules(svc, "sid", "cid")
        by_name = {row["name"]: row for row in out}
        # Plugin row carries type=plugin + its enabled flag.
        assert by_name["p1"]["type"] == "plugin"
        assert by_name["p1"]["description"] == "pd"
        assert by_name["p1"]["enabled"] is False
        # Tool rows carry the on/off state and origin of their source row.
        assert by_name["nt1"]["type"] == "native_tool"
        assert by_name["nt1"]["enabled"] is False
        assert by_name["nt1"]["origin"]["label"] == "builtin"
        assert by_name["nt1"]["schema"] == {"k": 1}
        assert by_name["web_search"]["type"] == "tool"
        assert by_name["web_search"]["options"] == {"backend": "duckduckgo"}
        assert by_name["web_search"]["enabled"] is True
        assert by_name["web_search"]["origin"]["label"] == "package:kt-biome"

    def test_swallows_per_type_errors(self, monkeypatch):
        def raise_runtime(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(mod.creature_plugins, "plugin_inventory", raise_runtime)
        monkeypatch.setattr(
            mod.creature_state, "native_tool_inventory", lambda e, sid, cid: []
        )
        monkeypatch.setattr(
            mod.creature_state, "tool_inventory", lambda e, sid, cid: []
        )
        svc = _FakeService()
        # plugin path raises, but native_tool succeeds → returns empty list.
        out = mod.list_modules(svc, "sid", "cid")
        assert out == []

    def test_key_error_propagates(self, monkeypatch):
        def raise_key(*args, **kwargs):
            raise KeyError("missing")

        monkeypatch.setattr(mod.creature_plugins, "plugin_inventory", raise_key)
        with pytest.raises(KeyError):
            mod.list_modules(_FakeService(), "sid", "cid")


# ── get_module_options ──────────────────────────────────────────


class TestGetModuleOptions:
    def test_plugin(self, monkeypatch):
        def fake_get(e, sid, cid, name):
            return {"name": name, "schema": {"k": 1}, "options": {"a": 1}}

        monkeypatch.setattr(mod.creature_plugins, "get_plugin_options", fake_get)
        out = mod.get_module_options(_FakeService(), "sid", "cid", "plugin", "p1")
        assert out == {
            "type": "plugin",
            "name": "p1",
            "schema": {"k": 1},
            "options": {"a": 1},
        }

    def test_native_tool_known(self, monkeypatch):
        monkeypatch.setattr(
            mod.creature_state,
            "native_tool_inventory",
            lambda e, sid, cid: [
                {"name": "nt1", "option_schema": {"k": 1}, "values": {"a": 1}}
            ],
        )
        out = mod.get_module_options(_FakeService(), "sid", "cid", "native_tool", "nt1")
        # schema/options are remapped from the inventory entry's keys.
        assert out == {
            "type": "native_tool",
            "name": "nt1",
            "schema": {"k": 1},
            "options": {"a": 1},
        }

    def test_native_tool_missing(self, monkeypatch):
        monkeypatch.setattr(
            mod.creature_state, "native_tool_inventory", lambda e, sid, cid: []
        )
        with pytest.raises(KeyError):
            mod.get_module_options(
                _FakeService(), "sid", "cid", "native_tool", "missing"
            )

    def test_tool_known(self, monkeypatch):
        monkeypatch.setattr(
            mod.creature_state,
            "tool_inventory",
            lambda e, sid, cid: [
                {
                    "name": "web_search",
                    "option_schema": {"backend": {"type": "enum"}},
                    "values": {"backend": "duckduckgo"},
                }
            ],
        )
        out = mod.get_module_options(_FakeService(), "sid", "cid", "tool", "web_search")
        assert out["type"] == "tool"
        assert out["options"] == {"backend": "duckduckgo"}

    def test_unknown_type(self):
        with pytest.raises(ValueError):
            mod.get_module_options(_FakeService(), "sid", "cid", "ghost", "p1")


# ── set_module_options ──────────────────────────────────────────


class TestSetModuleOptions:
    def test_plugin(self, monkeypatch):
        called = []

        def fake_set(e, sid, cid, name, values):
            called.append((sid, cid, name, values))
            return {"ok": True}

        monkeypatch.setattr(mod.creature_plugins, "set_plugin_options", fake_set)
        out = mod.set_module_options(
            _FakeService(), "sid", "cid", "plugin", "p1", {"a": 1}
        )
        assert out == {"ok": True}
        assert called == [("sid", "cid", "p1", {"a": 1})]

    def test_native_tool(self, monkeypatch):
        called = []

        def fake_set(e, sid, cid, name, values):
            called.append(values)
            return {"applied": True}

        monkeypatch.setattr(mod.creature_state, "set_native_tool_options", fake_set)
        out = mod.set_module_options(
            _FakeService(), "sid", "cid", "native_tool", "nt1", None
        )
        assert out == {"applied": True}
        # None values become empty dict before passthrough.
        assert called == [{}]

    def test_tool(self, monkeypatch):
        called = []

        def fake_set(e, sid, cid, name, values):
            called.append((name, values))
            return {"backend": "deepseek"}

        monkeypatch.setattr(mod.creature_state, "set_tool_options", fake_set)
        out = mod.set_module_options(
            _FakeService(),
            "sid",
            "cid",
            "tool",
            "web_search",
            {"backend": "deepseek"},
        )
        assert out == {"backend": "deepseek"}
        assert called == [("web_search", {"backend": "deepseek"})]


# ── toggle_module ───────────────────────────────────────────────


class TestToggleModule:
    async def test_plugin(self, monkeypatch):
        async def fake_toggle(e, sid, cid, name):
            return {"enabled": True}

        monkeypatch.setattr(mod.creature_plugins, "toggle_plugin", fake_toggle)
        out = await mod.toggle_module(_FakeService(), "sid", "cid", "plugin", "p1")
        assert out == {"enabled": True}

    async def test_tool_is_turned_off_and_on(self, monkeypatch, tmp_path):
        agent = await _tool_agent(tmp_path)
        _install_creature(monkeypatch, agent)
        out = await mod.toggle_module(_FakeService(), "sid", "cid", "tool", "probe")
        assert out == {"name": "probe", "enabled": False, "warning": ""}
        assert agent.is_tool_enabled("probe") is False
        out = await mod.toggle_module(_FakeService(), "sid", "cid", "tool", "probe")
        assert out["enabled"] is True

    async def test_native_tool_is_turned_off(self, monkeypatch, tmp_path):
        agent = await _tool_agent(tmp_path)
        _install_creature(monkeypatch, agent)
        out = await mod.toggle_module(
            _FakeService(), "sid", "cid", "native_tool", "hosted"
        )
        assert out["enabled"] is False
        assert agent.disabled_tools == frozenset({"hosted"})

    async def test_tool_toggle_rejects_the_wrong_kind(self, monkeypatch, tmp_path):
        agent = await _tool_agent(tmp_path)
        _install_creature(monkeypatch, agent)
        with pytest.raises(KeyError):
            await mod.toggle_module(_FakeService(), "sid", "cid", "tool", "hosted")
        with pytest.raises(KeyError):
            await mod.toggle_module(
                _FakeService(), "sid", "cid", "native_tool", "probe"
            )
        assert agent.disabled_tools == frozenset()
