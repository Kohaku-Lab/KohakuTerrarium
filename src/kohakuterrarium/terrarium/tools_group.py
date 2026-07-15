"""Register communication tools for all creatures and mutation tools by privilege."""

from typing import Any

# Direct submodule imports trigger registration without cycling through package init.
import kohakuterrarium.terrarium.tools_group_channel as _channel_mod  # noqa: F401
import kohakuterrarium.terrarium.tools_group_lifecycle as _lifecycle_mod  # noqa: F401
import kohakuterrarium.terrarium.tools_group_send as _send_mod  # noqa: F401
import kohakuterrarium.terrarium.tools_group_status as _status_mod  # noqa: F401
import kohakuterrarium.terrarium.tools_group_wire as _wire_mod  # noqa: F401
from kohakuterrarium.builtins.tool_catalog import get_builtin_tool

#: Communication tools that enforce authorization per call.
ENGINE_BASIC_TOOL_NAMES: tuple[str, ...] = (
    "send_channel",
    "group_send",
)

#: Graph mutation tools reserved for privileged creatures.
PRIVILEGED_TOOL_NAMES: tuple[str, ...] = (
    "group_status",
    "group_add_node",
    "group_spawn_child",
    "group_remove_node",
    "group_start_node",
    "group_stop_node",
    "group_pause_node",
    "group_resume_node",
    "group_kill_node",
    "group_channel",
    "group_wire",
)

#: Complete group tool surface for compatibility and introspection.
GROUP_TOOL_NAMES: tuple[str, ...] = ENGINE_BASIC_TOOL_NAMES + PRIVILEGED_TOOL_NAMES


def _register_named(agent: Any, names: tuple[str, ...]) -> None:
    """Register missing tools and refresh the prompt when its surface changes.

    Unsupported registries and individual registration failures are tolerated for
    lightweight hosts; executor registration mirrors the agent registry when present.
    """
    registry = getattr(agent, "registry", None)
    executor = getattr(agent, "executor", None)
    if registry is None:
        return
    register = getattr(registry, "register_tool", None)
    get_tool = getattr(registry, "get_tool", None)
    if not callable(register):
        return
    registered_any = False
    for name in names:
        if callable(get_tool) and get_tool(name) is not None:
            continue
        tool = get_builtin_tool(name)
        if tool is None:
            continue
        try:
            register(tool)
        except Exception:
            continue
        registered_any = True
        if executor is not None:
            try:
                executor.register_tool(tool)
            except Exception:
                pass
    if registered_any:
        refresh = getattr(agent, "refresh_system_prompt", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass


def force_register_basic_tools(agent: Any) -> None:
    """Register communication tools on an engine-backed creature."""
    _register_named(agent, ENGINE_BASIC_TOOL_NAMES)


def force_register_privileged_tools(agent: Any) -> None:
    """Register graph mutation tools on a privileged creature."""
    _register_named(agent, PRIVILEGED_TOOL_NAMES)


def force_register_group_tools(agent: Any) -> None:
    """Register both tool tiers for backward compatibility."""
    _register_named(agent, GROUP_TOOL_NAMES)
