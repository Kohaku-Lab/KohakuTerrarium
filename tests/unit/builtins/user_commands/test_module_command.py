"""Unit tests for the runtime module command: tool options and turning tools off / on."""

from types import SimpleNamespace

import kohakuterrarium as kt
from kohakuterrarium.builtins.user_commands import module as module_command
from kohakuterrarium.core.config.types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    ToolConfigItem,
)
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.modules.tool.origin import ToolOrigin
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.testing.llm import ScriptedLLM


class _Tool:
    is_provider_native = False
    description = "search"

    def runtime_option_schema(self):
        return {
            "backend": {
                "type": "enum",
                "values": ["duckduckgo", "deepseek"],
                "default": "duckduckgo",
                "disabled_values": {"deepseek": "configure key"},
            }
        }


class _Registry:
    def list_tools(self):
        return ["web_search"]

    def get_tool(self, name):
        return _Tool() if name == "web_search" else None


class _Options:
    def __init__(self):
        self.values = {"backend": "duckduckgo"}

    def get(self, name):
        return dict(self.values)

    def set(self, name, values):
        if values:
            self.values.update(values)
        else:
            self.values = {"backend": "duckduckgo"}
        return dict(self.values)


def _agent():
    return SimpleNamespace(
        plugins=None,
        registry=_Registry(),
        native_tool_options=None,
        tool_options=_Options(),
    )


class TestOrdinaryToolModuleCommand:
    def test_inventory_and_show_include_tool_and_unavailable_reason(self):
        agent = _agent()
        module = module_command._inventory(agent)[0]

        assert module["type"] == "tool"
        rendered = module_command._render_show_module(module)
        assert "tool/web_search" in rendered
        assert "deepseek — configure key" in rendered

    def test_set_and_reset_route_through_tool_options(self):
        agent = _agent()

        changed = module_command._do_set(agent, ["web_search", "backend", "deepseek"])
        reset = module_command._do_reset(agent, ["web_search"])

        assert "deepseek" in changed.output
        assert "tool/web_search" in reset.output
        assert agent.tool_options.values == {"backend": "duckduckgo"}


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
        return f"{self._name} tool"

    @property
    def execution_mode(self):
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output="ran")


async def _real_agent(tmp_path):
    cfg = AgentConfig(
        name="mod_agent",
        system_prompt="Test.",
        include_hints_in_prompt=False,
        tool_format="bracket",
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
        tools=[ToolConfigItem(name="read")],
    )
    agent = await kt.Agent.build(
        cfg, llm=ScriptedLLM(["ok"]), tools=[_Probe("probe"), _Probe("info")]
    )
    agent.add_tool(_Probe("lab_query"), origin=ToolOrigin("mcp", "sandbox-lab"))
    agent.add_tool(_Probe("hosted_search", native=True))
    return agent


async def _run(agent, args):
    return await module_command.ModuleCommand().execute(
        args, UserCommandContext(agent=agent)
    )


class TestToolSwitchCommand:
    async def test_list_shows_every_tool_with_origin_and_state(self, tmp_path):
        agent = await _real_agent(tmp_path)
        agent.set_tool_enabled("probe", False)
        out = (await _run(agent, "")).output
        tools_part = out.split("\nTools\n", 1)[1]
        rows = {line.split()[1]: line for line in tools_part.splitlines()[:6]}
        assert rows["read"].lstrip().startswith("●") and "builtin" in rows["read"]
        assert rows["probe"].lstrip().startswith("○") and "runtime" in rows["probe"]
        assert "mcp:sandbox-lab" in rows["lab_query"]
        assert "Native tools" in out and "hosted_search" in out
        assert "/module disable <tool>" in out

    async def test_disable_and_enable_a_tool(self, tmp_path):
        agent = await _real_agent(tmp_path)
        off = await _run(agent, "disable probe")
        assert off.error is None and off.output == "Tool 'probe' turned off."
        assert agent.is_tool_enabled("probe") is False
        assert "`probe`" not in agent.get_system_prompt()

        on = await _run(agent, "enable probe")
        assert on.output == "Tool 'probe' turned on."
        assert agent.is_tool_enabled("probe") is True

        await _run(agent, "toggle tool/probe")
        assert agent.is_tool_enabled("probe") is False

    async def test_provider_native_tool_switches_through_its_own_type(self, tmp_path):
        agent = await _real_agent(tmp_path)
        result = await _run(agent, "disable native_tool/hosted_search")
        assert result.output == "Tool 'hosted_search' turned off."
        assert agent.disabled_tools == frozenset({"hosted_search"})

    async def test_disabling_a_framework_tool_warns(self, tmp_path):
        agent = await _real_agent(tmp_path)
        result = await _run(agent, "disable info")
        assert result.output.splitlines() == [
            "Tool 'info' turned off.",
            "Warning: 'info' is a framework tool; while it is off, the agent "
            "can no longer read tool and skill documentation.",
        ]

    async def test_show_names_the_origin(self, tmp_path):
        agent = await _real_agent(tmp_path)
        out = (await _run(agent, "show lab_query")).output
        assert out.splitlines()[0] == "tool/lab_query  [enabled]"
        assert "from mcp:sandbox-lab" in out

    async def test_unknown_module_is_an_error(self, tmp_path):
        agent = await _real_agent(tmp_path)
        result = await _run(agent, "disable ghost")
        assert result.error == "Module not found: 'ghost'"
        assert agent.disabled_tools == frozenset()
