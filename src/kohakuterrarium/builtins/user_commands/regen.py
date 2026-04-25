"""Regen command — regenerate the last assistant response."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


@register_user_command("regen")
class RegenCommand(BaseUserCommand):
    name = "regen"
    aliases = ["regenerate", "retry"]
    description = "Regenerate the last assistant response with current settings"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        if not context.agent:
            return UserCommandResult(error="No agent context.")
        await context.agent.regenerate_last_response()
        return UserCommandResult(output="Regenerating last response...")
