"""Persistence history — read-only on-disk history per target.

Paths use ``/{session_name}/history[/{target}]`` so the router can be
mounted under ``/api/sessions`` for URL preservation.

Saved sessions open the SQLite file in a worker thread. A LIVE session
(addressed by graph_id or by its file stem while still running) REUSES
the engine's already-open store on the event loop instead — a second
open of an actively-written store is unreliable on POSIX
(``SQLITE_IOERR`` on the tables the live writer touched), and the loop
serializes these reads with the writer.
"""

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.persistence.live_paths import live_store_entry
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.persistence.history import (
    history_from_store,
    history_index_from_store,
    history_index_payload,
    history_payload,
)
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.terrarium.creature_ops import agent_live_job_ids
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


async def _resolve_saved_path(session_name: str) -> Path:
    """Resolve a saved session's on-disk path; 404 if unknown."""
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return path


def _live_job_ids_for_graph(
    service: TerrariumService, graph_id: str
) -> set[str] | None:
    """Union of in-flight job ids across the live graph's creatures.

    Returns ``None`` when there is no host-local engine (lab host) or the
    id doesn't resolve to a live graph — the saved-history case, where
    every unfinished job is genuinely dead and its interrupted terminal
    is correct. When live, the ids let ``session_history_payload`` skip
    synthesising an ``interrupted`` terminal for still-running work.
    """
    engine = host_engine_or_none(service)
    if engine is None:
        return None
    try:
        graph = engine.get_graph(graph_id)
    except KeyError:
        return None
    live: set[str] = set()
    for creature_id in graph.creature_ids:
        try:
            agent = engine.get_creature(creature_id).agent
        except KeyError:
            continue
        if agent is not None:
            live |= agent_live_job_ids(agent)
    return live


def _live_session_name(store: SessionStore, session_name: str) -> str:
    """Payload display name: the store's file stem when available."""
    path = getattr(store, "_path", None)
    return Path(path).stem if path else session_name


@router.get("/{session_name}/history")
async def get_session_history_index(
    session_name: str,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Return session metadata and available read-only history targets."""
    entry = live_store_entry(service, session_name)
    if entry is not None:
        _, store = entry
        return history_index_from_store(store, _live_session_name(store, session_name))
    path = await _resolve_saved_path(session_name)
    return await asyncio.to_thread(history_index_payload, path)


@router.get("/{session_name}/history/{target}")
async def get_session_history(
    session_name: str,
    target: str,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Return history for an agent/root/channel target.

    For a LIVE session the still-running jobs are threaded through so an
    in-flight background sub-agent isn't shown as ``interrupted``; a
    saved session passes ``None`` and keeps read-only semantics.
    """
    target = unquote(target)
    entry = live_store_entry(service, session_name)
    if entry is not None:
        graph_id, store = entry
        live_job_ids = _live_job_ids_for_graph(service, graph_id)
        return history_from_store(
            store, _live_session_name(store, session_name), target, live_job_ids
        )
    path = await _resolve_saved_path(session_name)
    return await asyncio.to_thread(history_payload, path, target, None)
