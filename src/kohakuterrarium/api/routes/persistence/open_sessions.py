"""Aggregate live and user-open saved conversations for the application rail."""

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service, resolve_request_session_dir
from kohakuterrarium.api.routes.persistence._executor import (
    run_in_persistence_executor,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.persistence.session_index import get_session_index_default
from kohakuterrarium.studio.persistence.session_index.reconcile import reconcile
from kohakuterrarium.studio.persistence.store import resolve_session_path_in
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.studio.sessions.registry import stores_for
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


def _path_key(path: str | Path) -> str:
    resolved = str(Path(path).expanduser().resolve(strict=False))
    return os.path.normcase(resolved)


def _store_for_runtime(service: TerrariumService, runtime_id: str):
    store = stores_for(service).get(runtime_id)
    if store is not None and not getattr(store, "_closed", False):
        return store
    engine = host_engine_or_none(service)
    if engine is None:
        return None
    store = getattr(engine, "_session_stores", {}).get(runtime_id)
    if store is not None and not getattr(store, "_closed", False):
        return store
    return None


def _live_rows(
    service: TerrariumService,
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    rows: list[dict[str, Any]] = []
    live_paths: set[str] = set()
    live_conversation_ids: set[str] = set()
    used_ids: set[str] = set()

    for listing in lifecycle.list_sessions(service):
        try:
            session = lifecycle.get_session(service, listing.session_id)
        except KeyError:
            continue
        store = _store_for_runtime(service, listing.session_id)
        meta: dict[str, Any] = {}
        saved_name: str | None = None
        if store is not None:
            try:
                meta = store.load_meta()
            except Exception:
                meta = {}
            path = Path(store.path)
            live_paths.add(_path_key(path))
            saved_name = normalize_session_stem(path)
        else:
            registry_meta = lifecycle.meta_for(service).get(listing.session_id) or {}
            meta = dict(registry_meta)
            remote_path = str(meta.get("remote_session_path") or "")
            if remote_path:
                saved_name = normalize_session_stem(Path(remote_path))

        conversation_id = str(meta.get("conversation_id") or "") or None
        if conversation_id is not None:
            live_conversation_ids.add(conversation_id)
        row_id = conversation_id or saved_name or listing.session_id
        if row_id in used_ids:
            row_id = listing.session_id
        used_ids.add(row_id)
        is_terrarium = (
            meta.get("config_type") == "terrarium"
            or session.has_root
            or len(session.creatures) > 1
        )
        rows.append(
            {
                "id": row_id,
                "conversation_id": conversation_id,
                "runtime_id": listing.session_id,
                "saved_name": saved_name,
                "config_name": session.name,
                "type": "terrarium" if is_terrarium else "creature",
                "status": "running",
                "is_live": True,
                "pwd": session.pwd or str(meta.get("pwd", "") or ""),
                "node_id": listing.node_id,
                "creatures": list(session.creatures),
                "last_active": str(meta.get("last_active") or session.created_at or ""),
            }
        )
    return rows, live_paths, live_conversation_ids


def _dormant_row(row: dict[str, Any]) -> dict[str, Any]:
    agents = [str(agent) for agent in (row.get("agents") or [])]
    saved_name = str(row.get("name", "") or "")
    display_name = str(row.get("terrarium_name", "") or "")
    if not display_name:
        display_name = agents[0] if agents else saved_name
    conversation_id = str(row.get("conversation_id") or "")
    return {
        "id": conversation_id,
        "conversation_id": conversation_id,
        "runtime_id": None,
        "saved_name": saved_name,
        "config_name": display_name,
        "type": ("terrarium" if row.get("config_type") == "terrarium" else "creature"),
        "status": str(row.get("status", "") or "paused"),
        "is_live": False,
        "pwd": str(row.get("pwd", "") or ""),
        "node_id": str(row.get("node_id", "") or "_host"),
        "creatures": [{"name": agent} for agent in agents],
        "last_active": str(row.get("last_active", "") or ""),
    }


def build_open_session_rows(
    service: TerrariumService, session_dir: Path
) -> list[dict[str, Any]]:
    """Return active rows plus saved rows explicitly marked as open."""
    session_dir = Path(session_dir)
    live_rows, live_paths, live_conversation_ids = _live_rows(service)
    used_ids = {str(row["id"]) for row in live_rows}

    index = get_session_index_default(session_dir)
    reconcile(index, session_dir, full=False)
    dormant_rows: list[dict[str, Any]] = []
    for indexed in index.iter_entries():
        if indexed.get("conversation_open") is not True:
            continue
        if indexed.get("status") == "completed":
            continue
        saved_name = str(indexed.get("name", "") or "")
        conversation_id = str(indexed.get("conversation_id") or "")
        if not saved_name or not conversation_id:
            continue
        if conversation_id in live_conversation_ids:
            continue
        path = resolve_session_path_in(saved_name, session_dir)
        if path is not None and _path_key(path) in live_paths:
            continue
        row = _dormant_row(indexed)
        if row["id"] in used_ids:
            continue
        used_ids.add(str(row["id"]))
        dormant_rows.append(row)

    rows = live_rows + dormant_rows
    rows.sort(key=lambda row: str(row.get("last_active", "")), reverse=True)
    return rows


@router.post("/open/{conversation_id}/end")
async def end_open_conversation(
    conversation_id: str,
    service: TerrariumService = Depends(get_service),
    session_dir: Path = Depends(resolve_request_session_dir),
) -> dict[str, str]:
    """End a live or dormant conversation without implicitly resuming it."""
    rows = await asyncio.to_thread(build_open_session_rows, service, session_dir)
    row = next(
        (item for item in rows if item.get("conversation_id") == conversation_id),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="open conversation not found")
    runtime_id = row.get("runtime_id")
    if runtime_id:
        await lifecycle.end_session(service, str(runtime_id))
    else:
        saved_name = str(row.get("saved_name") or "")
        path = resolve_session_path_in(saved_name, session_dir=session_dir)
        if path is None:
            raise HTTPException(status_code=404, detail="saved conversation not found")
        store = SessionStore(path)
        try:
            store.set_conversation_open(False)
            store.update_status("completed")
            store.checkpoint()
        finally:
            store.close(update_status=False)
        index = get_session_index_default(session_dir=session_dir)
        reconcile(index, session_dir=session_dir)
    return {"status": "ended", "conversation_id": conversation_id}


@router.get("/open")
async def list_open_sessions(
    session_dir: Path = Depends(resolve_request_session_dir),
    service: TerrariumService = Depends(get_service),
):
    """Return conversations that are live or not explicitly ended by the user."""
    return await run_in_persistence_executor(
        build_open_session_rows, service, session_dir
    )
