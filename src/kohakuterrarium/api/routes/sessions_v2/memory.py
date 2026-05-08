"""Sessions memory — FTS5 / vector / hybrid search over a saved session.

Path is ``/{session_name}/memory/search`` so the router can be mounted
under ``/api/sessions`` for URL preservation: the frontend's
``sessionAPI.searchSession`` calls ``GET
/sessions/{name}/memory/search``.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.studio.sessions.memory_search import search_session_memory

router = APIRouter()


@router.get("/{session_name}/memory/search")
async def search_session_memory_route(
    session_name: str,
    q: str,
    mode: str = "auto",
    k: int = 10,
    agent: str | None = None,
) -> dict[str, Any]:
    """Search a session's memory via FTS5 or semantic / hybrid modes.

    Read-only. Wraps the existing ``SessionMemory.search()`` — no new
    indexing behavior. Modes: ``auto`` (default), ``fts``, ``semantic``,
    ``hybrid``.
    """
    # ``resolve_session_path_default`` walks ``~/.kohakuterrarium/sessions``
    # — a small but synchronous filesystem stat. Off-load it.
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")

    engine = get_engine()
    return await search_session_memory(
        path, q=q, mode=mode, k=k, agent=agent, engine=engine
    )
