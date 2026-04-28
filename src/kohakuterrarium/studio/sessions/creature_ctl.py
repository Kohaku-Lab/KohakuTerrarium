"""Per-creature control: interrupt + jobs + cancel + promote.

Replaces ``KohakuManager.agent_interrupt / agent_get_jobs /
agent_cancel_job`` and ``creature_interrupt / creature_get_jobs /
creature_cancel_job``.  Engine-backed via ``engine.get_creature(cid)``.
"""

from typing import Any

from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium.engine import Terrarium


def _get_agent(engine: Terrarium, session_id: str, creature_id: str) -> Any:
    creature = find_creature(engine, session_id, creature_id)
    return creature.agent


def interrupt(engine: Terrarium, session_id: str, creature_id: str) -> None:
    """Interrupt the creature's current turn."""
    _get_agent(engine, session_id, creature_id).interrupt()


def list_jobs(engine: Terrarium, session_id: str, creature_id: str) -> list[dict]:
    """Return the creature's running tool + sub-agent jobs."""
    agent = _get_agent(engine, session_id, creature_id)
    jobs = [j.to_dict() for j in agent.executor.get_running_jobs()]
    jobs.extend(j.to_dict() for j in agent.subagent_manager.get_running_jobs())
    return jobs


async def cancel_job(
    engine: Terrarium, session_id: str, creature_id: str, job_id: str
) -> bool:
    """Cancel one running tool / sub-agent job.  Returns True on hit."""
    agent = _get_agent(engine, session_id, creature_id)
    if agent._interrupt_direct_job(job_id):
        return True
    if await agent.executor.cancel(job_id):
        return True
    return await agent.subagent_manager.cancel(job_id)


def promote_job(
    engine: Terrarium, session_id: str, creature_id: str, job_id: str
) -> bool:
    """Promote a running direct job to background.  Returns True on hit."""
    agent = _get_agent(engine, session_id, creature_id)
    return bool(agent._promote_handle(job_id))
