"""``/spawn <recipe>`` — spawn a new creature into the engine.

Requires the focused creature to be **privileged** (the recipe-root
or a user-spawned top-level creature). The engine's ``add_creature``
loads the recipe and starts the new creature.
"""

from kohakuterrarium.builtins.user_commands.registry import register_user_command
from kohakuterrarium.modules.user_command.base import (
    BaseUserCommand,
    CommandLayer,
    UserCommandContext,
    UserCommandResult,
)


@register_user_command("spawn")
class SpawnCommand(BaseUserCommand):
    name = "spawn"
    aliases = []
    description = "Spawn a new creature from a recipe (privileged focus only)"
    layer = CommandLayer.AGENT

    async def _execute(
        self, args: str, context: UserCommandContext
    ) -> UserCommandResult:
        recipe = (args or "").strip()
        if not recipe:
            return UserCommandResult(error="usage: /spawn <recipe-path-or-name>")
        engine = (context.extra or {}).get("engine")
        cid = (context.extra or {}).get("creature_id", "")
        if engine is None or not cid:
            return UserCommandResult(error="no creature context")
        try:
            focus = engine.get_creature(cid)
        except Exception:
            return UserCommandResult(error=f"unknown focused creature: {cid}")
        if not getattr(focus, "is_privileged", False):
            return UserCommandResult(
                error="/spawn requires a privileged focused creature"
            )
        try:
            spawned = await engine.add_creature(recipe)
        except Exception as e:  # pragma: no cover - depends on engine errors
            return UserCommandResult(error=f"spawn failed: {e}")
        name = getattr(spawned, "name", recipe)
        return UserCommandResult(output=f"Spawned {name}")
