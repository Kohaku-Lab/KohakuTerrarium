"""Per-agent tool switch: turn any registered tool off or on.

A turned-off tool leaves the model-visible catalog (``core/tools/gate.py``),
is refused by the executor when called anyway, and is saved with the session.
"""

from typing import Any

from kohakuterrarium.core.agent.history.selection import persist_tool_selection
from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.modules.tool.origin import BUILTIN_ORIGIN, ToolOrigin
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

FRAMEWORK_TOOL_EFFECTS: dict[str, str] = {
    "info": "the agent can no longer read tool and skill documentation",
    "skill": "the agent can no longer load procedural skills",
    "send_channel": "the creature can no longer send to its channels",
    "group_send": "the creature can no longer message its group",
}


def tool_switch_state(agent: Any, name: str) -> dict[str, Any]:
    """``{"origin": {kind, name, label}, "enabled": bool}`` for one tool of ``agent``."""
    registry = getattr(agent, "registry", None)
    get_origin = getattr(registry, "get_origin", None)
    origin = get_origin(name) if callable(get_origin) else None
    if not isinstance(origin, ToolOrigin):
        origin = BUILTIN_ORIGIN
    is_enabled = getattr(registry, "is_tool_enabled", None)
    return {
        "origin": origin.to_dict(),
        "enabled": bool(is_enabled(name)) if callable(is_enabled) else True,
    }


def switch_tool(
    agent: Any, name: str, enabled: bool | None, *, provider_native: bool | None = None
) -> dict[str, Any]:
    """Turn a registered tool on, off, or flip it (``enabled=None``).

    ``provider_native`` restricts the switch to provider-native tools (True)
    or ordinary tools (False). Raises :class:`NotFoundError` when no such
    tool is registered. Returns ``{"name", "enabled", "warning"}``.
    """
    registry = getattr(agent, "registry", None)
    tool = registry.get_tool(name) if registry is not None else None
    if tool is None or (
        provider_native is not None
        and bool(getattr(tool, "is_provider_native", False)) != provider_native
    ):
        raise NotFoundError(f"Unknown tool: {name!r}")
    target = (not agent.is_tool_enabled(name)) if enabled is None else bool(enabled)
    agent.set_tool_enabled(name, target)
    return {
        "name": name,
        "enabled": target,
        "warning": "" if target else framework_tool_warning(name),
    }


def framework_tool_warning(name: str) -> str:
    """Warning text for turning off a framework tool; empty for other tools."""
    effect = FRAMEWORK_TOOL_EFFECTS.get(name)
    if not effect:
        return ""
    return f"Warning: {name!r} is a framework tool; while it is off, {effect}."


class AgentToolEnablementMixin:
    """``set_tool_enabled`` / ``is_tool_enabled`` / ``disabled_tools`` for ``Agent``.

    Applies to this agent's own catalog only: sub-agents build their own
    registry from their config and keep every tool they list.
    """

    registry: Any

    @property
    def disabled_tools(self) -> frozenset[str]:
        """Names of the tools turned off for this agent."""
        return self.registry.disabled_tools

    def is_tool_enabled(self, name: str) -> bool:
        """Whether ``name`` is on for this agent."""
        return self.registry.is_tool_enabled(name)

    def set_tool_enabled(self, name: str, enabled: bool) -> bool:
        """Turn a tool on or off; return whether its state changed.

        Raises :class:`NotFoundError` for a name that is not registered (a
        turned-off name that is no longer registered can still be turned on).
        The change is saved to the session and the system prompt is rebuilt.
        """
        registry = self.registry
        if registry.get_tool(name) is None and registry.is_tool_enabled(name):
            raise NotFoundError(f"Unknown tool: {name!r}")
        changed = registry.set_tool_enabled(name, bool(enabled))
        if not changed:
            return False
        persist_tool_selection(self, sorted(registry.disabled_tools))
        self._refresh_prompt_for_tools()
        logger.info("Tool switched", tool_name=name, enabled=bool(enabled))
        return True

    def restore_disabled_tools(self, names: list[str]) -> None:
        """Replace the turned-off set with saved names, registered or not."""
        registry = self.registry
        wanted = set(names)
        for name in registry.disabled_tools - wanted:
            registry.set_tool_enabled(name, True)
        for name in wanted:
            registry.set_tool_enabled(name, False)
        self._refresh_prompt_for_tools()

    def _refresh_prompt_for_tools(self) -> None:
        if getattr(self, "_controller_config", None) is None:
            return
        self.refresh_system_prompt()
