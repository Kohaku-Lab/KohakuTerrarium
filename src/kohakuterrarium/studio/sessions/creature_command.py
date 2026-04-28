"""Per-creature slash command execution.

Replaces ``KohakuManager.agent_execute_command /
creature_execute_command`` plus ``routes/agents.py:execute_command``
and ``routes/creatures.py:execute_creature_command``.
"""

from kohakuterrarium.builtins.user_commands import get_builtin_user_command
from kohakuterrarium.modules.user_command.base import UserCommandContext
from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium.engine import Terrarium


async def execute_command(
    engine: Terrarium,
    session_id: str,
    creature_id: str,
    command: str,
    args: str = "",
) -> dict:
    """Run a built-in slash command against a creature."""
    agent = find_creature(engine, session_id, creature_id).agent
    cmd = get_builtin_user_command(command)
    if cmd is None:
        raise ValueError(f"Unknown command: /{command}")
    context = UserCommandContext(
        agent=agent,
        session=getattr(agent, "session", None),
    )
    result = await cmd.execute(args, context)
    resp: dict = {
        "command": command,
        "output": result.output,
        "error": result.error,
        "success": result.success,
    }
    if result.data is not None:
        resp["data"] = result.data
    return resp
