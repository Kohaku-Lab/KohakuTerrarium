"""``/triggers`` slash command — list active triggers (read-only).

Mirrors :class:`TriggersTab.vue`: shows trigger_id, type, running
state, and creation timestamp for every trigger registered with the
agent's :class:`TriggerManager`. Read-only — adding or removing
triggers belongs in agent config, not at runtime.
"""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


@register_user_command("triggers")
class TriggersCommand(BaseUserCommand):
    name = "triggers"
    aliases = ["trigger"]
    description = "List active triggers (read-only)."
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        agent = context.agent
        if agent is None:
            return UserCommandResult(error="No agent context.")
        tm = getattr(agent, "trigger_manager", None)
        if tm is None:
            return UserCommandResult(output="No trigger manager on this agent.")
        try:
            entries = list(tm.list())
        except Exception as exc:
            return UserCommandResult(error=f"Failed to list triggers: {exc}")
        if not entries:
            return UserCommandResult(output="No active triggers.")
        lines = [f"Active triggers ({len(entries)}):"]
        for info in entries:
            status = "● running" if info.running else "○ idle"
            created = info.created_at.isoformat(timespec="seconds")
            lines.append(
                f"  {status:<10} {info.trigger_id:<24} "
                f"{info.trigger_type:<20} {created}"
            )
        return UserCommandResult(output="\n".join(lines))
