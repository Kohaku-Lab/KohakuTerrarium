"""Unit tests for tools in the Rich module picker: option forms, origin labels, on/off."""

from types import SimpleNamespace

import pytest

import kohakuterrarium as kt
from kohakuterrarium.core.agent.tools.options import ToolOptions
from kohakuterrarium.core.config.types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.modules.tool.origin import ToolOrigin
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.profiles.api_keys import save_api_key
from kohakuterrarium.builtins.tools.web.fetch import WebFetchTool
from kohakuterrarium.builtins.tools.web.search import WebSearchTool
from kohakuterrarium.builtins.cli_rich.dialogs.module_picker.picker import ModulePicker


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
        return _Tool()


class _Options:
    def get(self, name):
        return {"backend": "duckduckgo"}

    def set(self, name, values):
        return values


class TestModulePickerToolTab:
    @pytest.mark.parametrize(
        "tool_class,backend",
        [(WebSearchTool, "deepseek_api"), (WebFetchTool, "deepseek")],
    )
    def test_real_tool_keyboard_edit_saves_and_cancel_preserves(
        self, tool_class, backend
    ):
        save_api_key("deepseek", "test-only-key")
        tool = tool_class()
        registry = Registry()
        registry.register_tool(tool)
        agent = SimpleNamespace(
            plugins=None,
            native_tool_options=None,
            registry=registry,
            config=SimpleNamespace(name="cli-test"),
            session=SimpleNamespace(extra={}),
            session_store=None,
        )
        agent.tool_options = ToolOptions(agent)
        picker = ModulePicker(lambda: agent)
        picker.open(edit_target=f"tool/{tool.tool_name}")
        field = picker._form.fields[0]
        assert field.key == "backend"
        for _ in range(len(field.options)):
            if field.value == backend:
                break
            picker.handle_key("right")
        assert field.value == backend
        for _ in range(len(picker._form.fields)):
            picker.handle_key("enter")
        assert picker.mode == "list"
        assert tool.backend == backend
        assert agent.session.extra["tool_options"][tool.tool_name]["backend"] == backend
        picker.open(edit_target=f"tool/{tool.tool_name}")
        assert picker._form.fields[0].value == backend
        picker.handle_key("right")
        picker.handle_key("escape")
        assert tool.backend == backend

    def test_opens_tool_form_and_excludes_unavailable_enum_value(self):
        agent = SimpleNamespace(
            plugins=None,
            registry=_Registry(),
            native_tool_options=None,
            tool_options=_Options(),
        )
        picker = ModulePicker(lambda: agent)

        picker.open(edit_target="tool/web_search")

        assert picker.active_type == "tool"
        assert picker._form is not None
        field = picker._form.fields[0]
        assert field.options == ["duckduckgo"]
        assert field.unavailable == {"deepseek": "configure key"}


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


async def _agent_with_tools(tmp_path):
    cfg = AgentConfig(
        name="picker_agent",
        system_prompt="Test.",
        include_hints_in_prompt=False,
        agent_path=tmp_path,
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
    )
    agent = await kt.Agent.build(cfg, llm=ScriptedLLM(["ok"]), tools=[_Probe("info")])
    agent.add_tool(_Probe("lab_query"), origin=ToolOrigin("mcp", "sandbox-lab"))
    agent.add_tool(_Probe("hosted", native=True))
    return agent


def _select(picker, kind, name):
    picker.active_type = kind
    names = [m.name for m in picker._entries_by_type[kind]]
    picker._cursor[kind] = names.index(name)


class TestModulePickerToolSwitch:
    async def test_tool_rows_show_origin_and_state(self, tmp_path):
        agent = await _agent_with_tools(tmp_path)
        agent.set_tool_enabled("info", False)
        picker = ModulePicker(lambda: agent)
        picker.open()
        tools = {m.name: m for m in picker._entries_by_type["tool"]}
        assert tools["lab_query"].origin == "mcp:sandbox-lab"
        assert tools["lab_query"].enabled is True
        assert tools["info"].enabled is False
        natives = {m.name: m for m in picker._entries_by_type["native_tool"]}
        assert natives["hosted"].origin == "runtime"
        _select(picker, "tool", "lab_query")
        rendered = picker.render(120)
        assert "mcp:sandbox-lab" in rendered
        assert "on/off" in rendered

    async def test_t_turns_the_selected_tool_off_and_on(self, tmp_path):
        agent = await _agent_with_tools(tmp_path)
        picker = ModulePicker(lambda: agent)
        picker.open()
        _select(picker, "tool", "lab_query")
        picker.handle_text("t")
        assert agent.is_tool_enabled("lab_query") is False
        assert picker._flash == "Tool 'lab_query' turned off."
        lab = next(m for m in picker._entries_by_type["tool"] if m.name == "lab_query")
        assert lab.enabled is False
        _select(picker, "tool", "lab_query")
        picker.handle_text("t")
        assert agent.is_tool_enabled("lab_query") is True

    async def test_native_tool_and_framework_warning(self, tmp_path):
        agent = await _agent_with_tools(tmp_path)
        picker = ModulePicker(lambda: agent)
        picker.open()
        _select(picker, "native_tool", "hosted")
        picker.handle_text("t")
        assert agent.disabled_tools == frozenset({"hosted"})
        _select(picker, "tool", "info")
        picker.handle_text("t")
        assert picker._flash.startswith("Tool 'info' turned off. Warning:")

    async def test_toggle_of_a_vanished_tool_reports_failure(self, tmp_path):
        agent = await _agent_with_tools(tmp_path)
        picker = ModulePicker(lambda: agent)
        picker.open()
        _select(picker, "tool", "lab_query")
        agent.registry.unregister_tool("lab_query")
        picker.handle_text("t")
        assert picker._flash.startswith("Toggle failed: Unknown tool")
        assert agent.disabled_tools == frozenset()
