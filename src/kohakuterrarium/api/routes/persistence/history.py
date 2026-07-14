"""Persistence history — read-only on-disk history per target.

Paths use ``/{session_name}/history[/{target}]`` so the router can be
mounted under ``/api/sessions`` for URL preservation.

The payload builders open the session SQLite file synchronously, so
each handler runs them in a worker thread to keep the event loop
free for concurrent API calls.
"""

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.persistence.live_paths import live_store_path
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.persistence.history import (
    history_index_payload,
    history_payload,
)
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.terrarium.creature_ops import agent_live_job_ids
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


async def _resolve_history_path(
    session_name: str, service: TerrariumService
) -> tuple[Path, bool]:
    """Resolve ``session_name`` to a store file and whether it is LIVE.

    Live graph_id → attached store first (``is_live=True``), else the
    on-disk name (``is_live=False``); 404 if neither resolves.
    """
    live = live_store_path(service, session_name)
    if live is not None:
        return live, True
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return path, False


def _live_job_ids_for_session(
    service: TerrariumService, session_name: str
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
        graph = engine.get_graph(session_name)
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


@router.get("/{session_name}/history")
async def get_session_history_index(
    session_name: str,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Return session metadata and available read-only history targets."""
    path, _ = await _resolve_history_path(session_name, service)
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
    path, is_live = await _resolve_history_path(session_name, service)
    live_job_ids = _live_job_ids_for_session(service, session_name) if is_live else None
    return await asyncio.to_thread(history_payload, path, target, live_job_ids)
