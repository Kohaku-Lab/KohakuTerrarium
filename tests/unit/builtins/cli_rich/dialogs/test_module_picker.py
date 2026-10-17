"""Unit tests for ordinary tools in the Rich module picker."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent_tool_options import ToolOptions
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.api_keys import save_api_key
from kohakuterrarium.builtins.tools.web_fetch import WebFetchTool
from kohakuterrarium.builtins.tools.web_search import WebSearchTool
from kohakuterrarium.builtins.cli_rich.dialogs.module_picker import ModulePicker


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
