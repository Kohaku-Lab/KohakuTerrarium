"""Control live creature turns and jobs through TerrariumService.

Service routing reaches the creature's home node in multi-node deployments.
``session_id`` remains in the signatures for API compatibility, although the
globally unique creature ID determines routing.
"""

from kohakuterrarium.terrarium import TerrariumService


async def interrupt(
    service: "TerrariumService", session_id: str, creature_id: str
) -> None:
    """Interrupt the creature's current turn."""
    await service.interrupt(creature_id)


async def start(service: "TerrariumService", session_id: str, creature_id: str) -> None:
    """Start a stopped creature in place."""
    await service.start_creature(creature_id)


async def stop(service: "TerrariumService", session_id: str, creature_id: str) -> None:
    """Stop a running creature without removing it from its graph."""
    await service.stop_creature(creature_id)


async def list_jobs(
    service: "TerrariumService", session_id: str, creature_id: str
) -> list[dict]:
    """Return the creature's running tool + sub-agent jobs."""
    return await service.list_jobs(creature_id)


async def cancel_job(
    service: "TerrariumService", session_id: str, creature_id: str, job_id: str
) -> bool:
    """Cancel one running tool / sub-agent job.  Returns True on hit."""
    return await service.stop_job(creature_id, job_id)


async def promote_job(
    service: "TerrariumService", session_id: str, creature_id: str, job_id: str
) -> bool:
    """Promote a running direct job to background.  Returns True on hit."""
    return await service.promote_job(creature_id, job_id)
