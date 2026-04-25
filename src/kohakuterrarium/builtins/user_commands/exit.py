"""Exit command — graceful shutdown."""

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
        # For CLI/TUI: exit immediately (no confirmation)
        if context.input_module and hasattr(context.input_module, "_exit_requested"):
            context.input_module._exit_requested = True
            return UserCommandResult(output="")

        # For web frontend: return confirm payload
        return UserCommandResult(
            output="Exiting session.",
            data=ui_confirm(
                "Are you sure you want to exit this session?",
                action="exit",
                action_args="--force",
            ),
        )
