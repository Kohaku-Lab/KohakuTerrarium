"""Compact command — trigger manual context compaction."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_notify,
)


@register_user_command("compact")
class CompactCommand(BaseUserCommand):
    name = "compact"
    aliases = []
    description = "Compact conversation context now"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        if not context.agent:
            return UserCommandResult(error="No agent context.")
        mgr = context.agent.compact_manager
        if not mgr:
            return UserCommandResult(error="Compaction not configured.")
        if mgr.is_compacting:
            return UserCommandResult(
                output="Compaction already in progress.",
                data=ui_notify("Compaction already in progress", level="warning"),
            )
        if not mgr.trigger_compact():
            return UserCommandResult(
                output="Compaction ignored because another compact job is running.",
                data=ui_notify(
                    "Compaction ignored because another compact job is running",
                    level="warning",
                ),
            )
        return UserCommandResult(
            output="Compaction triggered.",
            data=ui_notify("Context compaction started", level="info"),
        )
