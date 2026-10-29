"""Model-facing tool catalog: plugin visibility combined with renderer availability.

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


def visible_catalog(
    registry: Any,
    plugins: Any = None,
    router: Any = None,
    context: Any = None,
) -> ToolVisibility | None:
    """Merge plugin tool visibility with renderer availability."""
    plugin_visibility = None
    collect = getattr(plugins, "collect_tool_visibility", None)
    if callable(collect):
        plugin_visibility = collect() if context is None else collect(context)
    gated = renderer_visibility(registry, router_renderers(router))
    if gated is None:
        return plugin_visibility
    return merge_visibility(plugin_visibility, gated)


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
