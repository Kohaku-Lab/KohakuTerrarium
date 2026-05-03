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

    # The frontend's ``sessionAPI.resume`` (api.js:399) expects
    # ``{instance_id, type, session_name}``; map a ``Session`` handle
    # into that legacy shape and stash the full body under ``session``
    # for any future caller that wants it.
    #
    # For creature sessions ``instance_id`` is the creature_id (matches
    # the ``agentAPI.list`` shape, which is what the frontend's
    # instance store keys on); for terrarium sessions it is the
    # graph-level session_id.  Without this dispatch the frontend would
    # navigate to ``/instances/<graph_id>``, fail to find that id in
    # the agent list (agents list by creature_id), and fall back to
    # probing ``/active/terrariums/{id}`` — opening a creature session
    # in the terrarium UI.
    if session.kind == "terrarium":
        instance_type = "terrarium"
        instance_id = session.session_id
    else:
        instance_type = "agent"
        first = session.creatures[0] if session.creatures else {}
        instance_id = (
            first.get("creature_id") or first.get("agent_id") or session.session_id
        )
    return {
        "instance_id": instance_id,
        "type": instance_type,
        "session_name": session.name,
        "session": asdict(session),
    }
