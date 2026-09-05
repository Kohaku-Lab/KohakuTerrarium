"""Interrupt creatures and manage their background jobs.

Service routing reaches remote home workers and retries once when home-node routing
is stale.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.studio.sessions import creature_ctl
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


@router.post("/{session_id}/creatures/{creature_id}/interrupt")
async def interrupt_creature(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        await creature_ctl.interrupt(service, session_id, cid)
        return {"status": "interrupted"}
    except ConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/jobs")
async def list_creature_jobs(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await creature_ctl.list_jobs(service, session_id, cid)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.post("/{session_id}/creatures/{creature_id}/tasks/{job_id}/stop")
async def stop_creature_job(
    session_id: str,
    creature_id: str,
    job_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        ok = await creature_ctl.cancel_job(service, session_id, cid, job_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    if not ok:
        raise HTTPException(404, f"Task not found or already completed: {job_id}")
    return {"status": "cancelled", "job_id": job_id}


@router.post("/{session_id}/creatures/{creature_id}/promote/{job_id}")
async def promote_creature_job(
    session_id: str,
    creature_id: str,
    job_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        ok = await creature_ctl.promote_job(service, session_id, cid, job_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    return {"status": "promoted" if ok else "not_found"}
