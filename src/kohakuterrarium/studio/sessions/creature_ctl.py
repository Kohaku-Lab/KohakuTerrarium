"""Per-creature control: interrupt + jobs + cancel + promote.

Routes via the :class:`TerrariumService` so multi-node lab-host
deployments hit the creature's home node automatically — no
``as_engine`` unwrap here.  Resolution by ``creature_id`` mirrors
``MultiNodeTerrariumService._route_per_creature``.

The ``session_id`` parameter is kept for backwards compatibility
with existing API routes (which thread it through unchanged) — it's
not actually needed for routing since ``creature_id`` is globally
unique, but the legacy signature lets the routes stay unchanged.
"""

from kohakuterrarium.terrarium import TerrariumService


async def interrupt(
    service: "TerrariumService", session_id: str, creature_id: str
) -> None:
    """Interrupt the creature's current turn."""
    await service.interrupt(creature_id)


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
