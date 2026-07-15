"""Start the focused creature or resolve and start a named creature."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.builtins.user_commands.stop import _resolve_target
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


@register_user_command("start")
class StartCommand(BaseUserCommand):
    name = "start"
    aliases = []
    description = "Start a stopped creature"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        target = _resolve_target(args, context)
        if target is None:
            return UserCommandResult(
                error=f"unknown creature: {args.strip() or 'focus'}"
            )
        if target.is_running:
            return UserCommandResult(output=f"{target.name} is already running")
        try:
            await target.start()
        except Exception as e:  # pragma: no cover
            return UserCommandResult(error=f"start failed: {e}")
        return UserCommandResult(output=f"Started {target.name}")
