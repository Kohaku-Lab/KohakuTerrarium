"""Unit tests for tool-visibility filtering in :mod:`kohakuterrarium.core.controller`.

Behavior-first: plugin contributions restrict native tool schemas by
category (tools vs sub-agents), provider-native tools obey the tool
restriction, and a missing manager leaves the catalog untouched.
"""

from types import SimpleNamespace

from kohakuterrarium.core.controller import Controller, _schema_allowed
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.plugin.base import ToolVisibility
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult


class _PlainTool(BaseTool):
    @property
    def tool_name(self) -> str:
        return "plain"

    @property
    def description(self) -> str:
        return "Plain tool."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        return ToolResult(output="ok")


class _NativeTool(_PlainTool):
    is_provider_native = True

    @property
    def tool_name(self) -> str:
        return "native"

    @property
    def description(self) -> str:
        return "Provider-native tool."


class _VisibilityManager:
    def __init__(self, visibility):
        self._visibility = visibility

    def collect_tool_visibility(self):
        return self._visibility


class _NoVisibilityManager:
    def collect_tool_visibility(self):
        return None


def _make_controller():
    registry = Registry()
    registry.register_tool(_PlainTool())
    registry.register_tool(_NativeTool())
    registry.register_subagent("worker", SimpleNamespace(description="Worker."))
    controller = Controller(llm=object(), registry=registry)
    return controller, registry


class TestSchemaAllowed:
    def test_tool_category_uses_allowed_tools(self):
        visibility = ToolVisibility(allowed_tools=frozenset({"plain"}))
        subagents = {"worker"}
        assert _schema_allowed("plain", subagents, visibility) is True
        assert _schema_allowed("other", subagents, visibility) is False

    def test_subagent_category_uses_allowed_subagents(self):
        visibility = ToolVisibility(allowed_subagents=frozenset({"worker"}))
        subagents = {"worker"}
        assert _schema_allowed("worker", subagents, visibility) is True
        # A non-subagent name belongs to the tool category, which is
        # unrestricted here — sub-agent restrictions never leak sideways.
        assert _schema_allowed("other", subagents, visibility) is True

    def test_none_category_means_unrestricted(self):
        visibility = ToolVisibility()
        assert _schema_allowed("anything", {"worker"}, visibility) is True


class TestControllerVisibility:
    def test_native_schemas_filtered_by_category(self):
        controller, _registry = _make_controller()
        controller.plugins = _VisibilityManager(
            ToolVisibility(
                allowed_tools=frozenset({"plain"}),
                allowed_subagents=frozenset({"worker"}),
            )
        )
        names = [schema.name for schema in controller._get_native_tool_schemas()]
        assert names == ["plain", "worker"]

    def test_empty_subagent_restriction_hides_subagents(self):
        controller, _registry = _make_controller()
        controller.plugins = _VisibilityManager(
            ToolVisibility(
                allowed_tools=frozenset({"plain"}),
                allowed_subagents=frozenset(),
            )
        )
        names = [schema.name for schema in controller._get_native_tool_schemas()]
        assert names == ["plain"]

    def test_provider_native_tools_obey_tool_restriction(self):
        controller, _registry = _make_controller()
        controller.plugins = _VisibilityManager(
            ToolVisibility(allowed_tools=frozenset({"plain"}))
        )
        assert [
            tool.tool_name for tool in controller._get_provider_native_tools()
        ] == []

    def test_provider_native_tools_pass_with_allowed_name(self):
        controller, _registry = _make_controller()
        controller.plugins = _VisibilityManager(
            ToolVisibility(allowed_tools=frozenset({"native"}))
        )
        assert [tool.tool_name for tool in controller._get_provider_native_tools()] == [
            "native"
        ]

    def test_manager_returning_none_keeps_full_catalog(self):
        controller, _registry = _make_controller()
        controller.plugins = _NoVisibilityManager()
        names = [schema.name for schema in controller._get_native_tool_schemas()]
        assert names == ["plain", "worker"]
        assert [tool.tool_name for tool in controller._get_provider_native_tools()] == [
            "native"
        ]

    def test_no_plugins_keeps_full_catalog(self):
        controller, _registry = _make_controller()
        controller.plugins = None
        names = [schema.name for schema in controller._get_native_tool_schemas()]
        assert names == ["plain", "worker"]
        assert [tool.tool_name for tool in controller._get_provider_native_tools()] == [
            "native"
        ]
