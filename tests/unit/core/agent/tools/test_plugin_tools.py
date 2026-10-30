"""Unit tests for :mod:`kohakuterrarium.core.agent.tools.plugin_tools`.

Tools a plugin contributes join the registry, executor and prompt with the
plugin, leave when it is disabled or removed, come back on enable, and a
name clash fails the load without changing the registry.
"""

import pytest

import kohakuterrarium as kt
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.errors import ConflictError, KTError, PluginToolConflictError
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.testing.llm import ScriptedLLM


def _tool_class(name):
    class _T(BaseTool):
        @property
        def tool_name(self) -> str:
            return name

        @property
        def description(self) -> str:
            return f"The {name} tool."

        @property
        def execution_mode(self) -> ExecutionMode:
            return ExecutionMode.DIRECT

        async def _execute(self, args, **kwargs):
            return ToolResult(output=name)

    return _T


def _plugin(plugin_name, *tool_names):
    calls = []

    class _P(BasePlugin):
        name = plugin_name

        def contribute_tools(self):
            calls.append(1)
            return [_tool_class(n)() for n in tool_names]

    plugin = _P()
    plugin.calls = calls
    return plugin


def _cfg(tmp_path):
    return AgentConfig(
        name="plugin_tools_agent",
        system_prompt="You are a test agent.",
        include_hints_in_prompt=False,
        tool_format="bracket",
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
    )


def _registered(agent, name):
    return (
        name in agent.registry.list_tools()
        and agent.executor.get_tool(name) is not None
        and f"`{name}`" in agent.get_system_prompt()
    )


class TestConstruction:
    async def test_plugin_tools_land_in_the_initial_prompt(self, tmp_path):
        agent = await kt.Agent.build(
            _cfg(tmp_path),
            llm=ScriptedLLM(["x"]),
            plugins=[_plugin("apps", "show_app")],
        )
        assert _registered(agent, "show_app")

    async def test_clash_with_an_existing_tool_fails_the_build(self, tmp_path):
        with pytest.raises(PluginToolConflictError, match="already registered"):
            await kt.Agent.build(
                _cfg(tmp_path),
                llm=ScriptedLLM(["x"]),
                tools=[_tool_class("show_app")()],
                plugins=[_plugin("apps", "show_app")],
            )

    async def test_same_tool_class_already_configured_is_kept_not_a_clash(
        self, tmp_path
    ):
        shared = _tool_class("show_chart")
        configured = shared()

        class _P(BasePlugin):
            name = "ui.declarative"

            def contribute_tools(self):
                return [shared()]

        agent = await kt.Agent.build(
            _cfg(tmp_path), llm=ScriptedLLM(["x"]), tools=[configured], plugins=[_P()]
        )
        assert agent.registry.get_tool("show_chart") is configured
        agent.plugins.disable("ui.declarative")
        assert agent.registry.get_tool("show_chart") is configured, "not the plugin's"
        agent.plugins.enable("ui.declarative")
        assert agent.registry.get_tool("show_chart") is configured

    async def test_two_plugins_with_the_same_tool_fail_the_build(self, tmp_path):
        with pytest.raises(PluginToolConflictError, match="both contribute"):
            await kt.Agent.build(
                _cfg(tmp_path),
                llm=ScriptedLLM(["x"]),
                plugins=[_plugin("a", "dup"), _plugin("b", "dup")],
            )

    def test_conflict_error_is_typed(self):
        assert issubclass(PluginToolConflictError, ConflictError)
        assert issubclass(PluginToolConflictError, KTError)


class TestBeforeStart:
    async def test_disable_and_enable_before_start_keep_tools_in_step(self, tmp_path):
        agent = await kt.Agent.build(
            _cfg(tmp_path),
            llm=ScriptedLLM(["x"]),
            plugins=[_plugin("apps", "show_app")],
        )
        agent.plugins.disable("apps")
        assert "show_app" not in agent.registry.list_tools()
        assert agent.executor.get_tool("show_app") is None
        assert "show_app" not in agent.get_system_prompt()
        agent.plugins.enable("apps")
        assert _registered(agent, "show_app")

    async def test_catalog_plugin_enabled_before_start_brings_its_tool(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg.plugins = [{"name": "ui.apps", "enabled": False}]
        agent = await kt.Agent.build(cfg, llm=ScriptedLLM(["x"]))
        assert not agent.plugins.is_enabled("ui.apps"), "config enabled: false wins"
        assert "show_app" not in agent.registry.list_tools()
        agent.plugins.enable("ui.apps")
        assert "show_app" in agent.registry.list_tools()
        assert agent.executor.get_tool("show_app") is not None
        await agent.start()
        try:
            assert "show_app" in agent.registry.list_tools()
            agent.plugins.disable("ui.apps")
            assert "show_app" not in agent.registry.list_tools()
        finally:
            await agent.stop()


class TestDefaultOn:
    async def test_ui_plugins_start_enabled_with_a_gated_tool(self, tmp_path):
        agent = await kt.Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["x"]))
        assert agent.plugins.is_enabled("ui.apps")
        assert agent.plugins.is_enabled("ui.declarative")
        assert not agent.plugins.is_enabled("sandbox"), "policy plugins stay off"
        assert "show_app" in agent.registry.list_tools()
        assert "show_app" not in agent.get_system_prompt(), "no client draws kt.app"

    async def test_a_clashing_default_plugin_stands_down(self, tmp_path):
        agent = await kt.Agent.build(
            _cfg(tmp_path),
            llm=ScriptedLLM(["x"]),
            plugins=[_plugin("my_apps", "show_app")],
        )
        assert not agent.plugins.is_enabled("ui.apps")
        assert agent.plugins.is_enabled("my_apps")
        assert type(agent.registry.get_tool("show_app")).__name__ == "_T"
        # The user's own explicit enable still reports the clash.
        with pytest.raises(PluginToolConflictError):
            agent.plugins.enable("ui.apps")
        assert not agent.plugins.is_enabled("ui.apps")

    async def test_a_configured_tool_keeps_its_name(self, tmp_path):
        agent = await kt.Agent.build(
            _cfg(tmp_path), llm=ScriptedLLM(["x"]), tools=[_tool_class("show_app")()]
        )
        assert not agent.plugins.is_enabled("ui.apps")
        assert agent.plugins.is_enabled("ui.declarative")
        assert type(agent.registry.get_tool("show_app")).__name__ == "_T"


class TestRuntimeMembership:
    async def test_disable_removes_and_enable_restores(self, tmp_path):
        plugin = _plugin("apps", "show_app", "read_app")
        agent = await kt.Agent.build(
            _cfg(tmp_path), llm=ScriptedLLM(["x"]), plugins=[plugin]
        )
        await agent.start()
        try:
            first = agent.registry.get_tool("show_app")
            agent.plugins.disable("apps")
            assert "show_app" not in agent.registry.list_tools()
            assert agent.executor.get_tool("read_app") is None
            assert "show_app" not in agent.get_system_prompt()
            agent.plugins.enable("apps")
            assert _registered(agent, "show_app") and _registered(agent, "read_app")
            assert agent.registry.get_tool("show_app") is first
            assert plugin.calls == [1]
        finally:
            await agent.stop()

    async def test_add_plugin_registers_and_unregister_removes(self, tmp_path):
        agent = await kt.Agent.build(_cfg(tmp_path), llm=ScriptedLLM(["x"]))
        await agent.start()
        try:
            await agent.add_plugin(_plugin("apps", "show_app"))
            assert _registered(agent, "show_app")
            agent.plugins.unregister("apps")
            assert "show_app" not in agent.registry.list_tools()
            assert "show_app" not in agent.get_system_prompt()
        finally:
            await agent.stop()

    async def test_add_plugin_clash_rolls_back(self, tmp_path):
        agent = await kt.Agent.build(
            _cfg(tmp_path), llm=ScriptedLLM(["x"]), tools=[_tool_class("busy")()]
        )
        await agent.start()
        try:
            own = agent.registry.get_tool("busy")
            with pytest.raises(PluginToolConflictError):
                await agent.add_plugin(_plugin("intruder", "busy", "extra"))
            assert agent.registry.get_tool("busy") is own
            assert "extra" not in agent.registry.list_tools()
            assert "intruder" not in {p["name"] for p in agent.plugins.list_plugins()}
        finally:
            await agent.stop()

    async def test_enable_clash_rolls_back(self, tmp_path):
        agent = await kt.Agent.build(
            _cfg(tmp_path),
            llm=ScriptedLLM(["x"]),
            plugins=[_plugin("apps", "show_app")],
        )
        await agent.start()
        try:
            agent.plugins.disable("apps")
            agent.add_tool(_tool_class("show_app")())
            own = agent.registry.get_tool("show_app")
            with pytest.raises(PluginToolConflictError):
                agent.plugins.enable("apps")
            assert not agent.plugins.is_enabled("apps")
            assert agent.registry.get_tool("show_app") is own
        finally:
            await agent.stop()

    async def test_replaced_plugin_tool_is_left_to_its_new_owner(self, tmp_path):
        agent = await kt.Agent.build(
            _cfg(tmp_path),
            llm=ScriptedLLM(["x"]),
            plugins=[_plugin("apps", "show_app")],
        )
        await agent.start()
        try:
            agent.add_tool(_tool_class("show_app")())
            replacement = agent.registry.get_tool("show_app")
            agent.plugins.disable("apps")
            assert agent.registry.get_tool("show_app") is replacement
        finally:
            await agent.stop()
