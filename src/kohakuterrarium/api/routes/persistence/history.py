"""Persistence history — read-only on-disk history per target.

Paths use ``/{session_name}/history[/{target}]`` so the router can be
mounted under ``/api/sessions`` for URL preservation.

The payload builders open the session SQLite file synchronously, so
each handler runs them in a worker thread to keep the event loop
free for concurrent API calls.
"""

import asyncio
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException

from kohakuterrarium.studio.persistence.history import (
    history_index_payload,
    history_payload,
)
from kohakuterrarium.studio.persistence.store import resolve_session_path_default

router = APIRouter()


@router.get("/{session_name}/history")
async def get_session_history_index(session_name: str) -> dict[str, Any]:
    """Return session metadata and available read-only history targets."""
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return await asyncio.to_thread(history_index_payload, path)


@router.get("/{session_name}/history/{target}")
async def get_session_history(session_name: str, target: str) -> dict[str, Any]:
    """Return read-only saved history for an agent/root/channel target."""
    target = unquote(target)
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return await asyncio.to_thread(history_payload, path, target)
