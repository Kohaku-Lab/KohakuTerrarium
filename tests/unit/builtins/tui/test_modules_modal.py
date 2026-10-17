"""Unit tests for ordinary tools in the Textual modules modal."""

from types import SimpleNamespace

import pytest
from textual.app import App
from textual.widgets import DataTable, Input, Select, Switch, TabbedContent

from kohakuterrarium.core.agent_tool_options import ToolOptions
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.llm.api_keys import save_api_key
from kohakuterrarium.builtins.tools.web_fetch import WebFetchTool
from kohakuterrarium.builtins.tools.web_search import WebSearchTool
from kohakuterrarium.builtins.tui.widgets.modules_modal import (
    ModuleEditModal,
    ModulesModal,
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
    @pytest.mark.parametrize(
        "tool_class,backend",
        [(WebSearchTool, "deepseek_api"), (WebFetchTool, "deepseek")],
    )
    async def test_real_tool_editor_saves_and_reopens(self, tool_class, backend):
        save_api_key("deepseek", "test-only-key")
        tool = tool_class()
        registry = Registry()
        registry.register_tool(tool)
        agent = SimpleNamespace(
            plugins=None,
            native_tool_options=None,
            registry=registry,
            config=SimpleNamespace(name="tui-test"),
            session=SimpleNamespace(extra={}),
            session_store=None,
        )
        agent.tool_options = ToolOptions(agent)
        app = App()
        async with app.run_test(size=(110, 48)) as pilot:
            await app.push_screen(ModulesModal(agent))
            await pilot.pause()
            app.screen.query_one(TabbedContent).active = "tab-tool"
            table = app.screen.query_one("#table-tool", DataTable)
            table.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ModuleEditModal)
            app.screen.query_one("#f-backend", Select).value = backend
            if tool.tool_name == "web_search":
                app.screen.query_one("#f-need_content", Switch).value = True
                app.screen.query_one("#f-localization", Input).value = "203.0.113.1"
            await pilot.click("#btn-save")
            await pilot.pause()
            assert isinstance(app.screen, ModulesModal)
            assert tool.backend == backend
            assert (
                agent.session.extra["tool_options"][tool.tool_name]["backend"]
                == backend
            )
            app.screen.query_one("#table-tool", DataTable).focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen.query_one("#f-backend", Select).value == backend
            if tool.tool_name == "web_search":
                assert tool.need_content is True
                assert tool.localization == "203.0.113.1"
                assert app.screen.query_one("#f-need_content", Switch).value is True
            app.screen.query_one("#f-backend", Select).value = (
                "duckduckgo" if tool.tool_name == "web_search" else "auto"
            )
            await pilot.click("#btn-cancel")
            await pilot.pause()
            assert tool.backend == backend

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
