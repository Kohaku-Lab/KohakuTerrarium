"""``/stop`` — stop the focused creature, or a creature by name.

Multi-creature aware: with no args, stops the focused creature. With
``/stop <name>`` stops a specific creature looked up via the engine
passed in ``context.extra["engine"]``.
"""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


def _resolve_target(name: str, context: UserCommandContext):
    """Look up the creature to act on; ``None`` if not found."""
    engine = (context.extra or {}).get("engine")
    if engine is None:
        return None
    target_name = (name or "").strip()
    if not target_name:
        # Default to the focused creature.
        cid = (context.extra or {}).get("creature_id", "")
        if not cid:
            return None
        try:
            return engine.get_creature(cid)
        except Exception:
            return None
    # Try by id first, then by name.
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
        except Exception as e:  # pragma: no cover - defensive
            return UserCommandResult(error=f"stop failed: {e}")
        return UserCommandResult(output=f"Stopped {target.name}")
