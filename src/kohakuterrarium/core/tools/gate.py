"""Model-facing tool catalog: plugin visibility, renderer availability and turned-off tools.

Native schemas and every prompt section that names tools read the catalog
through :func:`visible_catalog`, so a tool hidden here is hidden everywhere.
"""

from typing import Any

from kohakuterrarium.modules.plugin.base import ToolVisibility
from kohakuterrarium.modules.plugin.tool_visibility import merge_visibility
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def router_renderers(router: Any) -> dict[str, int]:
    """Return the renderer ids the router's attached outputs can draw."""
    probe = getattr(router, "ui_renderers", None)
    if not callable(probe):
        return {}
    try:
        renderers = probe()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("ui_renderers raised", error=str(e), exc_info=True)
        return {}
    return dict(renderers or {})


def tool_available(tool: Any, renderers: dict[str, int]) -> bool:
    """Whether every renderer the tool requires is attached."""
    required = getattr(tool, "requires_renderers", None) or ()
    return all(renderer in renderers for renderer in required)


def renderer_visibility(
    registry: Any, renderers: dict[str, int]
) -> ToolVisibility | None:
    """Restriction hiding tools whose renderers are missing; None when nothing is hidden."""
    if registry is None:
        return None
    names = list(registry.list_tools())
    available = [
        name for name in names if tool_available(registry.get_tool(name), renderers)
    ]
    if len(available) == len(names):
        return None
    return ToolVisibility(allowed_tools=frozenset(available))


def tool_turned_off_message(name: str) -> str:
    """The tool result an agent gets for calling a tool that is turned off."""
    return (
        f"Tool '{name}' is turned off for this agent. Use another tool, "
        f"or turn it on with /module enable {name}."
    )


def enabled_visibility(registry: Any) -> ToolVisibility | None:
    """Restriction hiding turned-off tools; None when every registered tool is on."""
    disabled = getattr(registry, "disabled_tools", None)
    if not isinstance(disabled, (set, frozenset)) or not disabled:
        return None
    names = list(registry.list_tools())
    enabled = [name for name in names if name not in disabled]
    if len(enabled) == len(names):
        return None
    return ToolVisibility(allowed_tools=frozenset(enabled))


def visible_catalog(
    registry: Any,
    plugins: Any = None,
    router: Any = None,
    context: Any = None,
) -> ToolVisibility | None:
    """Intersect plugin tool visibility, renderer availability and turned-off tools."""
    visibility = None
    collect = getattr(plugins, "collect_tool_visibility", None)
    if callable(collect):
        visibility = collect() if context is None else collect(context)
    for restriction in (
        renderer_visibility(registry, router_renderers(router)),
        enabled_visibility(registry),
    ):
        if restriction is not None:
            visibility = merge_visibility(visibility, restriction)
    return visibility


def is_tool_visible(name: str, visibility: ToolVisibility | None) -> bool:
    """Check a tool name against a catalog restriction."""
    if visibility is None or visibility.allowed_tools is None:
        return True
    return name in visibility.allowed_tools


def is_subagent_visible(name: str, visibility: ToolVisibility | None) -> bool:
    """Check a sub-agent name against a catalog restriction."""
    if visibility is None or visibility.allowed_subagents is None:
        return True
    return name in visibility.allowed_subagents


def visible_tool_names(registry: Any, visibility: ToolVisibility | None) -> list[str]:
    """Registered tool names the model may see, in registry order."""
    if registry is None:
        return []
    return [name for name in registry.list_tools() if is_tool_visible(name, visibility)]


def visible_subagent_names(
    registry: Any, visibility: ToolVisibility | None
) -> list[str]:
    """Registered sub-agent names the model may see, in registry order."""
    if registry is None:
        return []
    return [
        name
        for name in registry.list_subagents()
        if is_subagent_visible(name, visibility)
    ]
