"""Unit tests for :mod:`kohakuterrarium.core.agent.tools.enablement`.

A turned-off tool leaves the system prompt and the native schemas at once, is
refused with a tool error when the model calls it anyway, is saved to the
session store, and comes back when turned on.
"""

import json

import pytest

import kohakuterrarium as kt
from kohakuterrarium.core.agent.tools.enablement import (
    framework_tool_warning,
    switch_tool,
    tool_switch_state,
)
from kohakuterrarium.core.config.types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.modules.plugin.base import BasePlugin, ToolVisibility
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.modules.tool.origin import ToolOrigin
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM

REFUSAL = (
    "Tool 'probe' is turned off for this agent. Use another tool, "
    "or turn it on with /module enable probe."
)


class _Probe(BaseTool):
    def __init__(self, name="probe", native=False):
        super().__init__()
        self._name = name
        self.calls = 0
        self.is_provider_native = native

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Probe tool {self._name}."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self):
        return {"type": "object", "properties": {}}

    async def _execute(self, args, **kwargs):
        self.calls += 1
        return ToolResult(output="PROBE-RAN")


class _OnlyProbe(BasePlugin):
    name = "only_probe"

    def get_tool_visibility(self, context):
        return ToolVisibility(allowed_tools=frozenset({"probe"}))


def _cfg(tmp_path, tool_format="bracket"):
    return AgentConfig(
        name="switch_agent",
        system_prompt="You are a test agent.",
        include_hints_in_prompt=False,
        tool_format=tool_format,
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
        plugins=[
            {"name": "ui.apps", "enabled": False},
            {"name": "ui.declarative", "enabled": False},
        ],
    )


async def _agent(tmp_path, *, responses=("ok",), tool_format="bracket", tools=None):
    llm = ScriptedLLM(list(responses))
    agent = await kt.Agent.build(
        _cfg(tmp_path, tool_format),
        llm=llm,
        tools=tools if tools is not None else [_Probe(), _Probe("other")],
    )
    return agent, llm


def _native_names(agent):
    return {schema.name for schema in agent.controller._get_native_tool_schemas()}


class TestCatalog:
    async def test_turned_off_tool_leaves_the_prompt_and_returns(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        assert "`probe`" in agent.get_system_prompt()

        assert agent.set_tool_enabled("probe", False) is True
        assert "`probe`" not in agent.get_system_prompt()
        assert "`other`" in agent.get_system_prompt()
        assert agent.is_tool_enabled("probe") is False
        assert agent.disabled_tools == frozenset({"probe"})

        assert agent.set_tool_enabled("probe", True) is True
        assert "`probe`" in agent.get_system_prompt()
        assert agent.disabled_tools == frozenset()

    async def test_turned_off_tool_leaves_native_schemas(self, tmp_path):
        agent, _ = await _agent(tmp_path, tool_format="native")
        assert {"probe", "other"} <= _native_names(agent)
        agent.set_tool_enabled("probe", False)
        names = _native_names(agent)
        assert "probe" not in names and "other" in names

    async def test_plugin_visibility_and_off_intersect(self, tmp_path):
        agent, _ = await _agent(tmp_path, tool_format="native")
        await agent.add_plugin(_OnlyProbe())
        assert _native_names(agent) & {"probe", "other"} == {"probe"}
        agent.set_tool_enabled("probe", False)
        assert _native_names(agent) & {"probe", "other"} == set()

    async def test_same_state_twice_does_not_rebuild_the_prompt(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        agent.set_tool_enabled("probe", False)
        calls = []
        agent.refresh_system_prompt = lambda: calls.append(1)
        assert agent.set_tool_enabled("probe", False) is False
        assert calls == []

    async def test_unknown_tool_raises_not_found(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        with pytest.raises(NotFoundError, match="Unknown tool: 'ghost'"):
            agent.set_tool_enabled("ghost", False)
        assert agent.disabled_tools == frozenset()

    async def test_off_name_without_a_tool_can_still_be_turned_on(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        agent.restore_disabled_tools(["vanished"])
        assert agent.set_tool_enabled("vanished", True) is True
        assert agent.disabled_tools == frozenset()

    async def test_readded_tool_stays_off_and_takes_the_new_origin(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        agent.set_tool_enabled("probe", False)
        agent.add_tool(_Probe(), origin=ToolOrigin("mcp", "lab"))
        assert agent.is_tool_enabled("probe") is False
        assert agent.registry.get_origin("probe").label == "mcp:lab"
        assert "`probe`" not in agent.get_system_prompt()


class TestCallRefused:
    async def test_calling_an_off_tool_returns_the_refusal(self, tmp_path):
        probe = _Probe()
        agent, llm = await _agent(
            tmp_path,
            responses=("[/probe][probe/]", "done"),
            tools=[probe],
        )
        agent.set_tool_enabled("probe", False)
        await agent.start()
        try:
            result = await agent.run("go", raise_on_error=False)
        finally:
            await agent.stop()
        assert result.text.endswith("done")
        assert probe.calls == 0
        followup = json.dumps(llm.call_log[1], ensure_ascii=False)
        assert REFUSAL in followup
        assert "PROBE-RAN" not in followup

    async def test_turned_back_on_tool_runs_again(self, tmp_path):
        probe = _Probe()
        agent, llm = await _agent(
            tmp_path,
            responses=("[/probe][probe/]", "done"),
            tools=[probe],
        )
        agent.set_tool_enabled("probe", False)
        agent.set_tool_enabled("probe", True)
        await agent.start()
        try:
            await agent.run("go", raise_on_error=False)
        finally:
            await agent.stop()
        assert probe.calls == 1
        assert "PROBE-RAN" in json.dumps(llm.call_log[1], ensure_ascii=False)


class TestPersistence:
    async def test_switch_is_saved_to_the_attached_store(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            agent.attach_session_store(store)
            agent.set_tool_enabled("probe", False)
            assert json.loads(store.state["switch_agent:disabled_tools"]) == ["probe"]
            agent.set_tool_enabled("probe", True)
            assert json.loads(store.state["switch_agent:disabled_tools"]) == []
        finally:
            store.close(update_status=False)

    async def test_attach_saves_a_switch_made_before_the_store(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        agent.set_tool_enabled("other", False)
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            agent.attach_session_store(store)
            assert json.loads(store.state["switch_agent:disabled_tools"]) == ["other"]
        finally:
            store.close(update_status=False)

    async def test_restore_replaces_the_off_set_and_refreshes_the_prompt(
        self, tmp_path
    ):
        agent, _ = await _agent(tmp_path)
        agent.set_tool_enabled("other", False)
        agent.restore_disabled_tools(["probe", "not_yet_registered"])
        assert agent.disabled_tools == frozenset({"probe", "not_yet_registered"})
        prompt = agent.get_system_prompt()
        assert "`probe`" not in prompt and "`other`" in prompt
        agent.add_tool(_Probe("not_yet_registered"))
        assert agent.is_tool_enabled("not_yet_registered") is False


class TestHelpers:
    async def test_switch_tool_flips_and_reports(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        assert switch_tool(agent, "probe", None) == {
            "name": "probe",
            "enabled": False,
            "warning": "",
        }
        assert switch_tool(agent, "probe", None)["enabled"] is True
        assert switch_tool(agent, "probe", True)["enabled"] is True
        assert agent.is_tool_enabled("probe") is True

    async def test_switch_tool_checks_the_native_kind(self, tmp_path):
        agent, _ = await _agent(tmp_path, tools=[_Probe(), _Probe("web", native=True)])
        with pytest.raises(NotFoundError):
            switch_tool(agent, "probe", False, provider_native=True)
        with pytest.raises(NotFoundError):
            switch_tool(agent, "web", False, provider_native=False)
        assert (
            switch_tool(agent, "web", False, provider_native=True)["enabled"] is False
        )
        assert agent.disabled_tools == frozenset({"web"})

    async def test_switch_tool_unknown_name_raises(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        with pytest.raises(NotFoundError):
            switch_tool(agent, "ghost", False)

    async def test_switch_tool_warns_for_framework_tools(self, tmp_path):
        agent, _ = await _agent(tmp_path, tools=[_Probe("info")])
        result = switch_tool(agent, "info", False)
        assert result["warning"].startswith("Warning: 'info' is a framework tool")
        assert switch_tool(agent, "info", True)["warning"] == ""

    async def test_tool_switch_state_reports_origin_and_state(self, tmp_path):
        agent, _ = await _agent(tmp_path)
        agent.set_tool_enabled("other", False)
        assert tool_switch_state(agent, "probe") == {
            "origin": {"kind": "runtime", "name": "", "label": "runtime"},
            "enabled": True,
        }
        assert tool_switch_state(agent, "other")["enabled"] is False

    def test_tool_switch_state_without_registry_is_builtin_and_on(self):
        class _Bare:
            registry = None

        assert tool_switch_state(_Bare(), "x") == {
            "origin": {"kind": "builtin", "name": "", "label": "builtin"},
            "enabled": True,
        }

    def test_framework_tool_warning_only_for_framework_tools(self):
        assert "skill" in framework_tool_warning("skill")
        assert framework_tool_warning("bash") == ""
