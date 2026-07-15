"""Request an immediate terminal exit or return web confirmation data."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
    ui_confirm,
)


@register_user_command("exit")
class ExitCommand(BaseUserCommand):
    name = "exit"
    aliases = ["quit", "q"]
    description = "Exit the session"
    layer = CommandLayer.INPUT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        # Interactive terminal modules own their exit flag and do not require confirmation.
        if context.input_module and hasattr(context.input_module, "_exit_requested"):
            context.input_module._exit_requested = True
            return UserCommandResult(output="")

        # Frontends without an exit flag need a confirmation payload.
        return UserCommandResult(
            output="Exiting session.",
            data=ui_confirm(
                "Are you sure you want to exit this session?",
                action="exit",
                action_args="--force",
            ),
        )
