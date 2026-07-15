"""Provide a settings entry point and a fallback for unsupported frontends."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_notify,
)


@register_user_command("settings")
class SettingsCommand(BaseUserCommand):
    name = "settings"
    aliases = ["config"]
    description = "Open the interactive settings overlay (keys, providers, models, MCP)"
    layer = CommandLayer.INPUT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        # The Rich CLI intercepts this command, so execution indicates a frontend
        # without an overlay and must return an actionable fallback.
        return UserCommandResult(
            output=(
                "Settings overlay is only available in the Rich CLI.\n"
                "Use `kt config` from the shell for the same mutations, or\n"
                "edit ~/.kohakuterrarium/*.yaml directly."
            ),
            data=ui_notify(
                "Settings overlay unavailable in this frontend", level="warning"
            ),
        )
