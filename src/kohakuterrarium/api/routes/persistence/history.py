"""Persistence history — read-only on-disk history per target.

Paths use ``/{session_name}/history[/{target}]`` so mounting under
``/api/sessions`` preserves the public URLs.

Saved-session SQLite reads run in a worker thread. Live sessions reuse the
engine-owned store on the event loop because a second connection to an actively
written store can raise ``SQLITE_IOERR`` on POSIX; loop affinity also
serializes reads with the writer.
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
    """Resolve a saved session path or raise 404 when it is unknown."""
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return path


def _live_job_ids_for_graph(
    service: TerrariumService, graph_id: str
) -> set[str] | None:
    """Collect in-flight job IDs across every creature in a live graph.

    ``None`` means no host-local live graph was found, so unfinished persisted
    jobs may be represented as interrupted. For a live graph, the returned IDs
    prevent history rendering from marking active work as interrupted.
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
    """Use the live store's file stem as its display name when available."""
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
    """Return history for an agent, root, or channel target.

    Live job IDs prevent active background work from appearing interrupted.
    Saved sessions have no live-job set, so persisted unfinished work receives
    the normal terminal representation.
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
