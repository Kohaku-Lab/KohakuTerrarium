"""Per-agent registration of tools, commands, and sub-agents."""

from typing import Any, Callable, TypeVar

from kohakuterrarium.modules.tool.base import Tool, ToolInfo
from kohakuterrarium.modules.tool.origin import BUILTIN_ORIGIN, ToolOrigin
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class Registry:
    """Store the callable modules exposed by one agent.

    Each tool carries a :class:`ToolOrigin`. Turned-off tool names are kept
    by name, so a tool that is unregistered and registered again stays off.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._tool_infos: dict[str, ToolInfo] = {}
        self._tool_origins: dict[str, ToolOrigin] = {}
        self._disabled_tools: set[str] = set()
        self._subagents: dict[str, Any] = {}  # SubAgent type defined later
        self._commands: dict[str, Any] = {}  # Command handlers

    def register_tool(self, tool: Tool, origin: ToolOrigin | None = None) -> None:
        """Register a tool instance; ``origin`` defaults to ``builtin``."""
        tool_name = tool.tool_name
        self._tools[tool_name] = tool
        self._tool_infos[tool_name] = ToolInfo.from_tool(tool)
        self._tool_origins[tool_name] = origin or BUILTIN_ORIGIN
        logger.debug("Registered tool", tool_name=tool_name)

    def unregister_tool(self, tool_name: str) -> bool:
        """Remove a tool and report whether it was registered."""
        existed = tool_name in self._tools
        self._tools.pop(tool_name, None)
        self._tool_infos.pop(tool_name, None)
        self._tool_origins.pop(tool_name, None)
        if existed:
            logger.debug("Unregistered tool", tool_name=tool_name)
        return existed

    def get_origin(self, tool_name: str) -> ToolOrigin | None:
        """The origin of a registered tool, or None when it is not registered."""
        return self._tool_origins.get(tool_name)

    def set_tool_enabled(self, tool_name: str, enabled: bool) -> bool:
        """Turn a tool name on or off; return whether the state changed."""
        was_enabled = tool_name not in self._disabled_tools
        if enabled:
            self._disabled_tools.discard(tool_name)
        else:
            self._disabled_tools.add(tool_name)
        return was_enabled != bool(enabled)

    def is_tool_enabled(self, tool_name: str) -> bool:
        """Whether a tool name is on (names never turned off are on)."""
        return tool_name not in self._disabled_tools

    @property
    def disabled_tools(self) -> frozenset[str]:
        """Every turned-off tool name, registered or not."""
        return frozenset(self._disabled_tools)

    def get_tool(self, tool_name: str) -> Tool | None:
        """Get a registered tool by name."""
        return self._tools.get(tool_name)

    def get_tool_info(self, tool_name: str) -> ToolInfo | None:
        """Get tool info by name."""
        return self._tool_infos.get(tool_name)

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def get_tools_prompt(self) -> str:
        """Generate tool list for system prompt."""
        if not self._tool_infos:
            return ""

        lines = ["## Available Tools"]
        for info in self._tool_infos.values():
            lines.append(info.to_prompt_line())
        return "\n".join(lines)

    def register_command(
        self,
        command_name: str,
        handler: Callable[..., Any],
    ) -> None:
        """Register a framework command handler."""
        self._commands[command_name] = handler
        logger.debug("Registered command", command_name=command_name)

    def get_command(self, command_name: str) -> Callable[..., Any] | None:
        """Get command handler by name."""
        return self._commands.get(command_name)

    def list_commands(self) -> list[str]:
        """List all registered command names."""
        return list(self._commands.keys())

    def register_subagent(self, subagent_name: str, subagent: Any) -> None:
        """Register a sub-agent."""
        self._subagents[subagent_name] = subagent
        logger.debug("Registered subagent", subagent_name=subagent_name)

    def get_subagent(self, subagent_name: str) -> Any | None:
        """Get sub-agent by name."""
        return self._subagents.get(subagent_name)

    def list_subagents(self) -> list[str]:
        """List all registered sub-agent names."""
        return list(self._subagents.keys())

    def clear(self) -> None:
        """Clear all registrations."""
        self._tools.clear()
        self._tool_infos.clear()
        self._tool_origins.clear()
        self._disabled_tools.clear()
        self._subagents.clear()
        self._commands.clear()
