"""Idempotent tool + prompt-plugin injection for Drive-enabled creatures.

A Drive-enabled Terrarium calls :func:`install_drive_runtime` on every creature
it creates or adopts (and, later, elevates): it registers the five self-service
tools and the :class:`DriveRuntimePromptPlugin` with full ``add_tool`` /
``add_plugin`` bookkeeping, so the live system prompt refreshes and the tools are
executable at once. It is idempotent — a second run replaces the tools in place
and swaps the plugin's snapshot rather than duplicating either (design §9.3).

A Terrarium with no Drive runtime never calls this, so a standalone
``Agent.build()`` and a Drive-disabled engine receive none of it.
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
# The privileged graph tool whose presence marks an agent as privileged. It is
# force-registered before ``attach_creature`` on the add path and by
# ``assign_root`` on elevation, so its presence is the reliable privilege signal
# available to injection (which only receives the agent, not the Creature).
_PRIVILEGE_MARKER_TOOL = "group_status"


async def install_drive_runtime(agent: Any, runtime: Any) -> None:
    """Install (or refresh) the self-service tools + prompt plugin on ``agent``.

    Idempotent: re-running replaces each tool in place and swaps the prompt
    plugin's snapshot instead of duplicating. No-ops on an agent-like without
    the runtime-extension surface (test fakes)."""
    add_tool = getattr(agent, "add_tool", None)
    if not callable(add_tool):
        return
    snapshot = runtime.snapshot
    for tool in build_self_service_tools():
        add_tool(tool)
    # Privileged creatures additionally get the graph-scoped ``group_drive``
    # admin tool (design §9.3). Detected from the agent's registered privileged
    # group tools since injection receives only the agent.
    if _agent_is_privileged(agent):
        for tool in build_group_drive_tools():
            add_tool(tool)
    await _install_prompt_plugin(agent, snapshot)


def _agent_is_privileged(agent: Any) -> bool:
    """Whether ``agent`` carries the privileged graph tool surface."""
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
        # Second run: keep the single instance, swap its snapshot, refresh.
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
    """Swap the prompt plugin's snapshot on a creature and refresh the prompt.

    The live-apply half of a registry reconfigure (design §8.6). No-op when the
    creature never received the plugin."""
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
    """Whether ``agent`` already carries the self-service Drive tools."""
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
