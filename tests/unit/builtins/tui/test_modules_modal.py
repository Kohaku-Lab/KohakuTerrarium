"""Unit tests for ordinary tools in the Textual modules modal."""

from types import SimpleNamespace

from kohakuterrarium.builtins.tui.widgets.modules_modal import (
    _apply_options,
    _list_modules,
)


class _Tool:
    is_provider_native = False
    description = "search"

    def runtime_option_schema(self):
        return {"backend": {"type": "enum", "values": ["duckduckgo", "deepseek"]}}


class _Registry:
    def list_tools(self):
        return ["web_search"]

    def get_tool(self, name):
        return _Tool()


class _Options:
    def __init__(self):
        self.values = {"backend": "duckduckgo"}

    def get(self, name):
        return dict(self.values)

    def set(self, name, values):
        self.values.update(values)
        return dict(self.values)


class TestToolModulesModal:
    def test_inventory_and_apply_use_tool_options(self):
        helper = _Options()
        agent = SimpleNamespace(
            plugins=None,
            registry=_Registry(),
            native_tool_options=None,
            tool_options=helper,
        )

        module = _list_modules(agent)[0]
        applied = _apply_options(agent, module, {"backend": "deepseek"})

        assert module["type"] == "tool"
        assert applied["backend"] == "deepseek"
        assert helper.values["backend"] == "deepseek"
