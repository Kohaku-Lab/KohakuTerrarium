"""Persistence fork — fork a saved session at an event id.

Path is ``/{session_name}/fork`` so the router can be mounted under
``/api/sessions`` for URL preservation.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.persistence.live_paths import live_store_entry
from kohakuterrarium.api.schemas import ForkRequest, ForkResponse
from kohakuterrarium.studio.persistence.fork import fork_session_handler
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


@router.post("/{session_name}/fork", status_code=201)
async def fork_session(
    session_name: str,
    payload: ForkRequest,
    service: TerrariumService = Depends(get_service),
) -> ForkResponse:
    """Fork a saved session at ``at_event_id`` into a new ``.kohakutr``.

    Returns 201 with the child's session id + path. Returns 400 for
    bad ``at_event_id`` or invalid mutation, 409 when the fork would
    split an in-flight job, and 404 if the source cannot be found.

    A LIVE source (still attached to the engine) is forked through the
    engine's open store — a second open of an actively-written file is
    unreliable on POSIX (``SQLITE_IOERR``). It is flushed first so the
    fork point can land on this turn's freshest events.
    """
    live = live_store_entry(service, session_name)
    if live is not None:
        _, store = live
        store.flush()
        path = Path(getattr(store, "_path"))
    else:
        store = None
        path = await asyncio.to_thread(resolve_session_path_default, session_name)
        if path is None:
            raise HTTPException(404, f"Session not found: {session_name}")

    result = await fork_session_handler(
        path,
        at_event_id=payload.at_event_id,
        mutate_kind=payload.mutate.kind if payload.mutate is not None else None,
        mutate_args=payload.mutate.args if payload.mutate is not None else None,
        name=payload.name,
        store=store,
    )
    return ForkResponse(
        session_id=result["session_id"],
        fork_point=result["fork_point"],
        path=result["path"],
    )
