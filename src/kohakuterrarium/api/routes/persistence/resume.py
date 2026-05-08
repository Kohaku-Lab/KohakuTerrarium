"""Persistence resume — adopt a saved session into the live engine.

Path is ``/{session_name}/resume`` so the router can be mounted under
``/api/sessions`` (legacy URL preservation: the frontend's
``sessionAPI.resumeSession`` calls ``POST /sessions/{name}/resume``).

Returns the legacy resume response shape ``{instance_id, type,
session_name}`` expected by ``sessionAPI.resume`` (api.js:399) plus
the full :class:`Session` handle under ``session`` for callers that
want it.
"""

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.persistence.resume import resume_session as studio_resume
from kohakuterrarium.studio.persistence.store import resolve_session_path_default

router = APIRouter()


@router.post("/{session_name}/resume")
async def resume_session(session_name: str, engine=Depends(get_engine)):
    """Resume a saved session into the engine.

    Returns ``{instance_id, type, session_name, session}``.  The first
    three fields are the legacy frontend contract; ``session`` is the
    full :class:`Session` handle dataclass-as-dict.
    """
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(
            status_code=404, detail=f"Session not found: {session_name}"
        )
    try:
        session = await studio_resume(engine, path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Frontend resume expects ``{instance_id, type, session_name}``.
    # We always return the canonical session_id (graph_id) — the
    # frontend's instance store accepts session_id as the primary
    # handle. ``type`` is derived from the live creature count: a
    # solo session looks like an agent, multi-creature looks like a
    # terrarium. This matches how the dashboard categorizes sessions.
    instance_type = "terrarium" if len(session.creatures) > 1 else "agent"
    return {
        "instance_id": session.session_id,
        "type": instance_type,
        "session_name": session.name,
        "session": asdict(session),
    }
