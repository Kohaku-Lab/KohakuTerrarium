"""Unit tests for :mod:`kohakuterrarium.core.tools.gate`.

The catalog is plugin visibility intersected with renderer availability. A
tool requiring a renderer is hidden while no attached output reports it and
visible once one does; sub-agent restrictions pass through untouched.
"""

from types import SimpleNamespace

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.core.tools.gate import (
    is_subagent_visible,
    is_tool_visible,
    renderer_visibility,
    router_renderers,
    tool_available,
    visible_catalog,
    visible_subagent_names,
    visible_tool_names,
)
from kohakuterrarium.modules.plugin.base import ToolVisibility
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult


def _tool(name, renderers=()):
    class _T(BaseTool):
        requires_renderers = frozenset(renderers)

        @property
        def tool_name(self):
            return name

        @property
        def description(self):
            return name

        @property
        def execution_mode(self):
            return ExecutionMode.DIRECT

        async def _execute(self, args, **kwargs):
            return ToolResult(output="")

    return _T()


def _registry():
    reg = Registry()
    reg.register_tool(_tool("read"))
    reg.register_tool(_tool("show_app", {"kt.app"}))
    reg.register_tool(_tool("show_chart", {"kt.ui"}))
    reg.register_subagent("explore", SimpleNamespace(description="x"))
    return reg


class _Router:
    def __init__(self, renderers):
        self.renderers = renderers

    def ui_renderers(self):
        return self.renderers


class _Plugins:
    def __init__(self, visibility):
        self.visibility = visibility
        self.contexts = []

    def collect_tool_visibility(self, context=None):
        self.contexts.append(context)
        return self.visibility


class TestRouterRenderers:
    def test_router_without_the_method_reports_nothing(self):
        assert router_renderers(None) == {}
        assert router_renderers(object()) == {}

    def test_router_report_is_copied(self):
        source = {"kt.app": 1}
        got = router_renderers(_Router(source))
        assert got == {"kt.app": 1}
        got["kt.ui"] = 1
        assert source == {"kt.app": 1}

    def test_none_report_is_empty(self):
        assert router_renderers(_Router(None)) == {}


class TestToolAvailable:
    def test_tool_without_requirements_is_always_available(self):
        assert tool_available(_tool("read"), {}) is True

    def test_requirement_must_be_attached(self):
        tool = _tool("show_app", {"kt.app"})
        assert tool_available(tool, {}) is False
        assert tool_available(tool, {"kt.ui": 1}) is False
        assert tool_available(tool, {"kt.app": 1}) is True

    def test_every_requirement_must_be_attached(self):
        tool = _tool("both", {"kt.app", "kt.ui"})
        assert tool_available(tool, {"kt.app": 1}) is False
        assert tool_available(tool, {"kt.app": 1, "kt.ui": 1}) is True


class TestRendererVisibility:
    def test_nothing_hidden_means_no_restriction(self):
        reg = _registry()
        assert renderer_visibility(reg, {"kt.app": 1, "kt.ui": 1}) is None
        assert renderer_visibility(None, {}) is None

    def test_missing_renderers_hide_their_tools(self):
        visibility = renderer_visibility(_registry(), {"kt.ui": 1})
        assert visibility.allowed_tools == frozenset({"read", "show_chart"})
        assert visibility.allowed_subagents is None


class TestVisibleCatalog:
    def test_plugin_visibility_alone_when_no_tool_is_gated(self):
        plugin = ToolVisibility(allowed_tools=frozenset({"read"}))
        reg = _registry()
        got = visible_catalog(reg, _Plugins(plugin), _Router({"kt.app": 1, "kt.ui": 1}))
        assert got is plugin

    def test_plugin_and_renderer_restrictions_intersect(self):
        plugin = ToolVisibility(
            allowed_tools=frozenset({"read", "show_app"}),
            allowed_subagents=frozenset(),
        )
        got = visible_catalog(_registry(), _Plugins(plugin), _Router({}))
        assert got.allowed_tools == frozenset({"read"})
        assert got.allowed_subagents == frozenset()

    def test_no_plugins_and_no_router_hides_gated_tools(self):
        got = visible_catalog(_registry())
        assert got.allowed_tools == frozenset({"read"})

    def test_context_is_passed_only_when_given(self):
        plugins = _Plugins(None)
        visible_catalog(_registry(), plugins, _Router({}))
        visible_catalog(_registry(), plugins, _Router({}), context="ctx")
        assert plugins.contexts == [None, "ctx"]


class TestNameFilters:
    def test_unrestricted_catalog_shows_everything(self):
        reg = _registry()
        assert visible_tool_names(reg, None) == reg.list_tools()
        assert visible_subagent_names(reg, None) == ["explore"]
        assert is_tool_visible("x", ToolVisibility()) is True
        assert is_subagent_visible("x", ToolVisibility()) is True

    def test_restricted_catalog_keeps_registry_order(self):
        reg = _registry()
        visibility = ToolVisibility(
            allowed_tools=frozenset({"show_chart", "read"}),
            allowed_subagents=frozenset(),
        )
        assert visible_tool_names(reg, visibility) == ["read", "show_chart"]
        assert visible_subagent_names(reg, visibility) == []

    def test_missing_registry_lists_nothing(self):
        assert visible_tool_names(None, None) == []
        assert visible_subagent_names(None, None) == []
