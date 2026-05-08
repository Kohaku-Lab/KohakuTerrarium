"""Per-creature control routes — interrupt + jobs + cancel + promote.

Mounted at ``/api/sessions``; URLs land at
``/api/sessions/{session_id}/creatures/{creature_id}/...``.

Sync sub-functions (``interrupt``, ``list_jobs``, ``promote_job``) are
funnelled through ``asyncio.to_thread``: each touches the agent's
trigger / job manager which can hit small disk reads, and the
running-jobs panel polls them frequently enough that any blocking
read on the loop visibly stalls the rest of the UI.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.sessions import creature_ctl

router = APIRouter()


@router.post("/{session_id}/creatures/{creature_id}/interrupt")
async def interrupt_creature(
    session_id: str, creature_id: str, engine=Depends(get_engine)
):
    try:
        await asyncio.to_thread(
            creature_ctl.interrupt, engine, session_id, creature_id
        )
        return {"status": "interrupted"}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/jobs")
async def list_creature_jobs(
    session_id: str, creature_id: str, engine=Depends(get_engine)
):
    try:
        return await asyncio.to_thread(
            creature_ctl.list_jobs, engine, session_id, creature_id
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.post("/{session_id}/creatures/{creature_id}/tasks/{job_id}/stop")
async def stop_creature_job(
    session_id: str,
    creature_id: str,
    job_id: str,
    engine=Depends(get_engine),
):
    try:
        ok = await creature_ctl.cancel_job(engine, session_id, creature_id, job_id)
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
    engine=Depends(get_engine),
):
    try:
        ok = await asyncio.to_thread(
            creature_ctl.promote_job, engine, session_id, creature_id, job_id
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    return {"status": "promoted" if ok else "not_found"}
