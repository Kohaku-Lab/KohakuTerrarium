"""Execute slash commands against live Studio-managed creatures."""

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
    """Run a slash command through the creature's live aggregated registry.

    Plugin-contributed commands remain available, and the command receives the
    service, engine, focused creature, principal, and operator context. This local
    programmatic console defaults to operator access; HTTP derives authorization
    independently.
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
