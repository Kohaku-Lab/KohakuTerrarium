"""Read and message sub-agent conversations through their owning service."""

from typing import Any

from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.sessions.find import find_creature
from kohakuterrarium.terrarium.service.service import TerrariumService
from kohakuterrarium.terrarium.service.subagent_conversations import (
    _subagent_name_from_job_id,  # noqa: F401
    read_subagent_conversation as _read_local,
    send_to_subagent as _send_local,
)


def read_subagent_conversation(service, session_id, creature_id, **kwargs):
    """Read a local transcript synchronously; remote callers use the async accessor."""
    engine = host_engine_or_none(service)
    if engine is None:
        raise NotFoundError("remote sub-agent reads require the async accessor")
    agent = find_creature(engine, session_id, creature_id).agent
    return _read_local(agent, session_id, creature_id, **kwargs)


async def read_subagent_conversation_async(
    service: TerrariumService,
    session_id: str,
    creature_id: str,
    *,
    job_id: str | None = None,
    name: str | None = None,
    run: int | None = None,
) -> dict[str, Any]:
    """Read a worker-owned child while retaining the requested session identity."""
    result = await service.read_subagent_conversation(
        creature_id, job_id=job_id, name=name, run=run
    )
    return {**result, "session_id": session_id, "creature_id": creature_id}


async def send_to_subagent(
    service: TerrariumService,
    session_id: str,
    creature_id: str,
    name: str,
    content: str,
    *,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Message a running child or continue a user-side thread on its worker."""
    if callable(getattr(service, "send_to_subagent", None)):
        return await service.send_to_subagent(creature_id, name, content, job_id=job_id)
    engine = host_engine_or_none(service)
    if engine is None:
        raise NotFoundError("sub-agent service is unavailable")
    agent = find_creature(engine, session_id, creature_id).agent
    return await _send_local(agent, name, content, job_id=job_id)
