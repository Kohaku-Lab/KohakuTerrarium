"""Edit a session's name, or edit / regenerate its one-line summary.

``PUT /{session_name}/title`` names the session; ``PUT
/{session_name}/summary/text`` stores a hand-written summary (empty text
returns it to automatic writing); ``POST /{session_name}/summary/refresh``
regenerates it now. A live session is changed through its open store and its
index row refreshed at once; a saved file is opened, changed, re-indexed and
closed.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.persistence.live_paths import live_store_entry
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.studio.sessions import index_hooks
from kohakuterrarium.studio.sessions.lifecycle import rename_session
from kohakuterrarium.studio.sessions.summary.edit import (
    regenerate,
    reindex,
    set_title,
    set_user_summary,
)
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class SummaryTextBody(BaseModel):
    text: str = ""


class TitleBody(BaseModel):
    title: str = ""


async def _with_store(
    service: TerrariumService,
    session_name: str,
    action: Callable[[Any], Awaitable[dict]],
) -> dict:
    entry = live_store_entry(service, session_name)
    if entry is not None:
        graph_id, store = entry
        record = await action(store)
        hook = index_hooks.registry().get(graph_id)
        if hook is not None:
            await asyncio.to_thread(hook.flush)
        return record
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    store = await asyncio.to_thread(SessionStore, path)
    try:
        record = await action(store)
        await reindex(store)
        return record
    finally:
        await asyncio.to_thread(store.close, update_status=False)


@router.put("/{session_name}/summary/text")
async def put_summary_text(
    session_name: str,
    body: SummaryTextBody,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    record = await _with_store(
        service, session_name, lambda store: set_user_summary(store, body.text)
    )
    return {"summary": record}


@router.put("/{session_name}/title")
async def put_title(
    session_name: str,
    body: TitleBody,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Name a session; a running one is renamed live, empty text clears the name."""
    title = " ".join(body.title.split())
    entry = live_store_entry(service, session_name)
    if entry is not None and title:
        rename_session(service, entry[0], title)
        hook = index_hooks.registry().get(entry[0])
        if hook is not None:
            await asyncio.to_thread(hook.flush)
        return {"title": title}
    return await _with_store(
        service, session_name, lambda store: set_title(store, title)
    )


@router.post("/{session_name}/summary/refresh")
async def post_summary_refresh(
    session_name: str,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    record = await _with_store(service, session_name, regenerate)
    return {"summary": record}
