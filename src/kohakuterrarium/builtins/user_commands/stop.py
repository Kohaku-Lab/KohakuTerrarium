"""Stop the focused creature or resolve and stop a named creature."""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


def _resolve_target(name: str, context: UserCommandContext):
    """Resolve the focused or named creature, returning ``None`` if unavailable."""
    engine = (context.extra or {}).get("engine")
    if engine is None:
        return None
    target_name = (name or "").strip()
    if not target_name:
        cid = (context.extra or {}).get("creature_id", "")
        if not cid:
            return None
        try:
            return engine.get_creature(cid)
        except Exception:
            return None
    try:
        return engine.get_creature(target_name)
    except Exception:
        pass
    for c in engine.list_creatures():
        if c.name == target_name or c.creature_id == target_name:
            return c
    return None


@register_user_command("stop")
class StopCommand(BaseUserCommand):
    name = "stop"
    aliases = []
    description = "Stop the focused (or named) creature"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        target = _resolve_target(args, context)
        if target is None:
            return UserCommandResult(
                error=f"unknown creature: {args.strip() or 'focus'}"
            )
        if not target.is_running:
            return UserCommandResult(output=f"{target.name} is already stopped")
        try:
            await target.stop()
        except Exception as e:  # pragma: no cover
            return UserCommandResult(error=f"stop failed: {e}")
        return UserCommandResult(output=f"Stopped {target.name}")
