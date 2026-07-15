"""Idempotent tool + prompt-plugin injection for Drive-enabled creatures.

Drive-enabled terrariums install self-service tools and the runtime prompt plugin
on created, adopted, or elevated creatures. Installation uses the agent's normal
extension APIs so tools become executable and prompt changes become visible
immediately. Reinstallation replaces tools and refreshes the existing plugin
snapshot instead of duplicating state. Drive-disabled and standalone agents are
unchanged.
"""

from typing import Any

from kohakuterrarium.terrarium.drive.prompt import DriveRuntimePromptPlugin
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot
from kohakuterrarium.terrarium.drive.tools import (
    SELF_SERVICE_TOOL_NAMES,
    build_self_service_tools,
)
from kohakuterrarium.terrarium.drive.tools_group import build_group_drive_tools
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_PROMPT_PLUGIN_NAME = "drive_runtime"
# The graph-status tool is the stable marker that privileged graph tooling was
# installed before Drive injection.
_PRIVILEGE_MARKER_TOOL = "group_status"


async def install_drive_runtime(agent: Any, runtime: Any) -> None:
    """Install or refresh Drive tools and prompt state on an agent."""
    add_tool = getattr(agent, "add_tool", None)
    if not callable(add_tool):
        return
    snapshot = runtime.snapshot
    for tool in build_self_service_tools():
        add_tool(tool)
    # Privilege is inferred from trusted graph tooling already installed on the
    # agent, not from caller-supplied state.
    if _agent_is_privileged(agent):
        for tool in build_group_drive_tools():
            add_tool(tool)
    await _install_prompt_plugin(agent, snapshot)


def _agent_is_privileged(agent: Any) -> bool:
    """Return whether the agent carries privileged graph tooling."""
    registry = getattr(agent, "registry", None)
    get_tool = getattr(registry, "get_tool", None)
    if not callable(get_tool):
        return False
    return get_tool(_PRIVILEGE_MARKER_TOOL) is not None


async def _install_prompt_plugin(
    agent: Any, snapshot: EnabledRegistrySnapshot | None
) -> None:
    plugins = getattr(agent, "plugins", None)
    existing = plugins.get_plugin(_PROMPT_PLUGIN_NAME) if plugins is not None else None
    if existing is not None:
        # Preserve plugin identity so refreshes do not duplicate runtime state.
        if hasattr(existing, "set_snapshot"):
            existing.set_snapshot(snapshot)
        refresh = getattr(agent, "refresh_system_prompt", None)
        if callable(refresh):
            refresh()
        return
    add_plugin = getattr(agent, "add_plugin", None)
    if not callable(add_plugin):
        return
    await add_plugin(DriveRuntimePromptPlugin(snapshot))


def refresh_drive_prompt(agent: Any, snapshot: EnabledRegistrySnapshot | None) -> None:
    """Refresh the installed Drive prompt plugin with a registry snapshot."""
    plugins = getattr(agent, "plugins", None)
    plugin = plugins.get_plugin(_PROMPT_PLUGIN_NAME) if plugins is not None else None
    if plugin is None:
        return
    if hasattr(plugin, "set_snapshot"):
        plugin.set_snapshot(snapshot)
    refresh = getattr(agent, "refresh_system_prompt", None)
    if callable(refresh):
        refresh()


def has_drive_injection(agent: Any) -> bool:
    """Return whether every self-service Drive tool is installed."""
    registry = getattr(agent, "registry", None)
    get_tool = getattr(registry, "get_tool", None)
    if not callable(get_tool):
        return False
    return all(get_tool(name) is not None for name in SELF_SERVICE_TOOL_NAMES)


__all__ = [
    "has_drive_injection",
    "install_drive_runtime",
    "refresh_drive_prompt",
]
