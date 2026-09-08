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
from kohakuterrarium.session.history_paging import HistoryPagingError
from kohakuterrarium.api.routes.persistence.live_paths import live_store_entry
from kohakuterrarium.errors import (
    ConflictError,
    NotFoundError,
    SessionError,
    SessionNotFoundError,
)
from kohakuterrarium.session.history_records import history_detail
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.persistence.history import (
    history_from_store,
    history_index_from_store,
    history_index_payload,
    history_page_from_store,
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


def _live_is_processing(service: TerrariumService, graph_id: str, target: str) -> bool:
    """Whether the live target's agent (or any agent in the graph) is
    processing. Saved sessions have no live agent and report ``False``."""
    engine = host_engine_or_none(service)
    if engine is None:
        return False
    try:
        graph = engine.get_graph(graph_id)
    except KeyError:
        return False
    if not target.startswith("ch:"):
        for creature_id in graph.creature_ids:
            try:
                creature = engine.get_creature(creature_id)
            except KeyError:
                continue
            if getattr(creature, "name", None) == target:
                agent = getattr(creature, "agent", None)
                return bool(
                    agent is not None and getattr(agent, "is_processing", False)
                )
    for creature_id in graph.creature_ids:
        try:
            agent = engine.get_creature(creature_id).agent
        except KeyError:
            continue
        if agent is not None and getattr(agent, "is_processing", False):
            return True
    return False


def _saved_history_page(
    path: Path,
    target: str,
    *,
    stream: str,
    limit: int,
    before: str | None,
    after: str | None,
    history_id: str | None,
) -> dict[str, Any]:
    """Build a paged history slice for a saved (on-disk) session in a worker
    thread. The store is opened and closed within this call so a failed lookup
    never leaves a Windows file handle behind."""
    path = Path(path)
    if not path.exists():
        raise SessionNotFoundError(f"Session not found: {path}")
    store: SessionStore | None = None
    try:
        store = SessionStore(path)
        return history_page_from_store(
            store,
            session_id=path.stem,
            session_name=path.stem,
            target=target,
            stream=stream,
            limit=limit,
            before=before,
            after=after,
            history_id=history_id,
            is_processing=False,
        )
    except (NotFoundError, SessionError, HistoryPagingError):
        raise
    except Exception as e:
        raise SessionError(f"History page load failed: {e}") from e
    finally:
        if store is not None:
            store.close(update_status=False)


def _saved_history_detail(path: Path, target: str, **kwargs) -> dict:
    """Read one full record from a saved session with deterministic closure."""
    if not path.exists():
        raise SessionNotFoundError(str(path))
    store = SessionStore(path)
    try:
        return history_detail(store, target, session_id=path.stem, **kwargs)
    finally:
        store.close(update_status=False)


@router.get("/{session_name}/history/{target}/detail")
async def get_session_history_detail(
    session_name: str,
    target: str,
    stream: str,
    ref: str,
    history_id: str,
    service: TerrariumService = Depends(get_service),
) -> dict:
    """Retrieve the full value for a saved or live-store history preview."""
    target = unquote(target)
    try:
        entry = live_store_entry(service, session_name)
        if entry is not None:
            graph_id, store = entry
            return history_detail(
                store,
                target,
                session_id=graph_id,
                stream=stream,
                ref=ref,
                history_id=history_id,
            )
        path = await _resolve_saved_path(session_name)
        return await asyncio.to_thread(
            _saved_history_detail,
            path,
            target,
            stream=stream,
            ref=ref,
            history_id=history_id,
        )
    except ConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (NotFoundError, KeyError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except HistoryPagingError as exc:
        raise HTTPException(400, str(exc)) from exc


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
    paged: bool = False,
    stream: str = "events",
    limit: int = 400,
    before: str | None = None,
    after: str | None = None,
    history_id: str | None = None,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Return history for an agent, root, or channel target.

    Legacy (``paged`` omitted) returns the full history for live or saved
    sessions. With ``paged=true`` it returns one bounded cursor-driven page via
    ``history_page``, reusing the same session pager as the live creature
    route. Channels are addressed by the ``ch:`` prefix and always page the
    ``channel`` stream.
    """
    target = unquote(target)
    entry = live_store_entry(service, session_name)
    if entry is not None:
        graph_id, store = entry
        live_session_name = _live_session_name(store, session_name)
        if paged:
            live_job_ids = _live_job_ids_for_graph(service, graph_id) or set()
            try:
                return history_page_from_store(
                    store,
                    session_id=graph_id,
                    session_name=live_session_name,
                    target=target,
                    stream=stream,
                    limit=limit,
                    before=before,
                    after=after,
                    history_id=history_id,
                    live_job_ids=live_job_ids,
                    is_processing=_live_is_processing(service, graph_id, target),
                )
            except HistoryPagingError as exc:
                raise HTTPException(400, str(exc)) from exc
        live_job_ids = _live_job_ids_for_graph(service, graph_id)
        return history_from_store(store, live_session_name, target, live_job_ids)
    path = await _resolve_saved_path(session_name)
    if paged:
        try:
            return await asyncio.to_thread(
                _saved_history_page,
                path,
                target,
                stream=stream,
                limit=limit,
                before=before,
                after=after,
                history_id=history_id,
            )
        except HistoryPagingError as exc:
            raise HTTPException(400, str(exc)) from exc
    return await asyncio.to_thread(history_payload, path, target, None)
