"""Settings command — opens the interactive settings overlay.

In the Rich CLI, ``_handle_slash`` special-cases ``/settings`` and
``/config`` to open ``SettingsOverlay`` directly (same shape as the
``/model`` picker path). This command class exists so the entry shows
up in ``/help`` and the slash-command hint bar, and so the TUI / web
frontends can still receive a sensible fallback payload.
"""

from kohakuterrarium.builtins.user_commands import register_user_command
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
        # The Rich CLI intercepts /settings before execute() runs and
        # opens the overlay inline. Reaching here means we're on a
        # frontend that doesn't implement the overlay yet — surface a
        # friendly pointer to ``kt config`` instead of silently no-oping.
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
