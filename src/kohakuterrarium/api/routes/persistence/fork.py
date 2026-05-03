"""Persistence fork — fork a saved session at an event id.

Path is ``/{session_name}/fork`` so the router can be mounted under
``/api/sessions`` for URL preservation.
"""

import asyncio

from fastapi import APIRouter, HTTPException

from kohakuterrarium.api.schemas import ForkRequest, ForkResponse
from kohakuterrarium.studio.persistence.fork import fork_session_handler
from kohakuterrarium.studio.persistence.store import resolve_session_path_default

router = APIRouter()


@router.post("/{session_name}/fork", status_code=201)
async def fork_session(session_name: str, payload: ForkRequest) -> ForkResponse:
    """Fork a saved session at ``at_event_id`` into a new ``.kohakutr``.

    Returns 201 with the child's session id + path. Returns 400 for
    bad ``at_event_id`` or invalid mutation, 409 when the fork would
    split an in-flight job, and 404 if the source cannot be found.
    """
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")

    result = await fork_session_handler(
        path,
        at_event_id=payload.at_event_id,
        mutate_kind=payload.mutate.kind if payload.mutate is not None else None,
        mutate_args=payload.mutate.args if payload.mutate is not None else None,
        name=payload.name,
    )
    return ForkResponse(
        session_id=result["session_id"],
        fork_point=result["fork_point"],
        path=result["path"],
    )
