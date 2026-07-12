"""Per-creature slash command execution.

Replaces ``KohakuManager.agent_execute_command /
creature_execute_command`` plus ``routes/agents.py:execute_command``
and ``routes/creatures.py:execute_creature_command``.
"""

from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.terrarium.creature_ops import agent_execute_command
from kohakuterrarium.studio._runtime import as_engine


async def execute_command(
    service: "TerrariumService",
    session_id: str,
    creature_id: str,
    command: str,
    args: str = "",
    *,
    principal: str | None = None,
    is_operator: bool = True,
) -> dict:
    """Run a slash command against a creature (trusted local Studio console).

    Delegates to :func:`terrarium.creature_ops.agent_execute_command`, which
    resolves the target creature's LIVE aggregated registry (so plugin-contributed
    ``/goal`` is reachable) and threads the trusted context DTO (service / engine
    / focused creature / principal / operator) into the command. This is the
    programmatic local console, so ``is_operator`` defaults on; the HTTP surface
    derives its own principal/operator from auth and does not pass through here
    (design §11.5, R1-20/R1-21).
    """
    engine = as_engine(service)
    agent = find_creature(engine, session_id, creature_id).agent
    return await agent_execute_command(
        agent,
        command,
        args,
        service=service,
        engine=engine,
        creature_id=creature_id,
        principal=principal or "user:local",
        is_operator=is_operator,
    )
