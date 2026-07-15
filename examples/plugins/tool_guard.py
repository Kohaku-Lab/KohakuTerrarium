"""Tool Guard Plugin — block dangerous tool calls with an allowlist/blocklist.

Demonstrates:
  - pre_tool_execute: inspect tool_name and args before execution
  - PluginBlockError: raise to prevent execution (message becomes tool result)
  - Options: configure allowed/blocked tools via plugin options

Usage in config.yaml:
    plugins:
      - name: tool_guard
        type: custom
        module: examples.plugins.tool_guard
        class: ToolGuardPlugin
        options:
          blocked_tools: [bash, python]
          # An allowlist, when present, takes precedence over the blocklist.
          # allowed_tools: [read, write, edit, glob, grep, tree, think]
          blocked_commands: [rm -rf, sudo, shutdown, reboot]
"""

from typing import Any

from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class ToolGuardPlugin(BasePlugin):
    """Enforce tool allowlists, blocklists, and command-pattern restrictions."""

    name = "tool_guard"
    priority = 1  # Security policy must run before other tool-call transforms.

    def __init__(self, options: dict[str, Any] | None = None):
        opts = options or {}
        self._blocked: set[str] = set(opts.get("blocked_tools", []))
        self._allowed: set[str] | None = None
        if "allowed_tools" in opts:
            self._allowed = set(opts["allowed_tools"])
        self._blocked_commands: list[str] = opts.get("blocked_commands", [])

    async def on_load(self, context: PluginContext) -> None:
        mode = "allowlist" if self._allowed else "blocklist"
        logger.info("Tool guard active", mode=mode, agent=context.agent_name)

    async def pre_tool_execute(self, args: dict, **kwargs) -> dict | None:
        """Allow the call unchanged or raise an informative policy block."""
        tool_name = kwargs.get("tool_name", "")

        # An allowlist is authoritative when configured.
        if self._allowed is not None and tool_name not in self._allowed:
            logger.warning("Tool blocked (not in allowlist)", tool=tool_name)
            raise PluginBlockError(
                f"Tool '{tool_name}' is not in the allowed list. "
                f"Allowed: {', '.join(sorted(self._allowed))}"
            )

        # Without an allowlist, reject explicitly blocked tools.
        if tool_name in self._blocked:
            logger.warning("Tool blocked (in blocklist)", tool=tool_name)
            raise PluginBlockError(
                f"Tool '{tool_name}' is blocked by security policy. "
                "Use a different approach."
            )

        # Command-pattern checks cover executable payloads inside shell tools.
        if tool_name in ("bash", "python") and self._blocked_commands:
            command = args.get("command", "") or args.get("code", "")
            for pattern in self._blocked_commands:
                if pattern in command:
                    logger.warning(
                        "Command blocked",
                        tool=tool_name,
                        pattern=pattern,
                    )
                    raise PluginBlockError(
                        f"Command contains blocked pattern: '{pattern}'. "
                        "This operation is not permitted."
                    )

        return None  # None preserves the original arguments.
