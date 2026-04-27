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
            # ``trigger_compact`` returns ``False`` for three distinct
            # reasons; ``_last_skip_reason`` tells us which so the user
            # gets a precise message instead of a generic "busy" line.
            reason = getattr(mgr, "_last_skip_reason", "") or ""
            messages = {
                "no_controller": (
                    "Compaction unavailable — no controller is bound to this agent.",
                    "Compaction unavailable",
                ),
                "too_short": (
                    "Nothing to compact yet — the conversation is too short.",
                    "Nothing to compact",
                ),
                "busy": (
                    "Compaction ignored because another compact job is running.",
                    "Compaction already running",
                ),
            }
            output, notify = messages.get(
                reason,
                (
                    "Compaction was not triggered.",
                    "Compaction not triggered",
                ),
            )
            return UserCommandResult(
                output=output,
                data=ui_notify(notify, level="warning"),
            )
        return UserCommandResult(
            output="Compaction triggered.",
            data=ui_notify("Context compaction started", level="info"),
        )
