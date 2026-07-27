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
from kohakuterrarium.api.routes.persistence.resume_coordinator import (
    resume_coordinator,
    session_coordination_key,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.persistence.session_index import get_session_index_default
from kohakuterrarium.studio.persistence.session_index.reconcile import reconcile
from kohakuterrarium.studio.persistence.store import resolve_session_path_in
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.studio.sessions.cluster_fold import cluster_groups
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
    groups = cluster_groups(service)
    meta_registry = lifecycle.meta_for(service)

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
            registry_meta = meta_registry.get(listing.session_id) or {}
            meta = dict(registry_meta)
            remote_path = str(meta.get("remote_session_path") or "")
            if remote_path:
                saved_name = normalize_session_stem(Path(remote_path))

        # A folded live cluster has one listing but several persisted member
        # files. Suppress every member mirror from the dormant half of the rail.
        for member_id in groups.get(listing.session_id, {listing.session_id}):
            member_meta = meta_registry.get(member_id) or {}
            member_path = str(member_meta.get("remote_session_path") or "")
            if member_path:
                live_paths.add(_path_key(member_path))
            member_conversation_id = str(member_meta.get("conversation_id") or "")
            if member_conversation_id:
                live_conversation_ids.add(member_conversation_id)

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


def _saved_conversation_paths(path: Path, session_dir: Path) -> list[Path]:
    """Resolve cluster members that share the selected saved conversation."""
    selected = SessionStore.open_readonly(path)
    try:
        conversation_id = str(selected.meta.get("conversation_id") or "")
        raw_members = selected.meta.get("cluster_members")
    finally:
        selected.close(update_status=False)

    paths = [path]
    if isinstance(raw_members, list):
        for member in raw_members:
            sid = member.get("sid") if isinstance(member, dict) else None
            if not isinstance(sid, str) or not sid:
                continue
            candidate = resolve_session_path_in(sid, session_dir=session_dir)
            if candidate is None:
                continue
            candidate_store = SessionStore.open_readonly(candidate)
            try:
                candidate_id = str(candidate_store.meta.get("conversation_id") or "")
            finally:
                candidate_store.close(update_status=False)
            if conversation_id and candidate_id == conversation_id:
                paths.append(candidate)

    unique: dict[str, Path] = {}
    for candidate in paths:
        unique.setdefault(_path_key(candidate), candidate)
    return list(unique.values())


def _end_saved_conversation(path: Path, session_dir: Path) -> None:
    """End every host-side member copy, rolling back partial marker writes."""
    updated: list[tuple[Path, bool, str]] = []
    try:
        for member_path in _saved_conversation_paths(path, session_dir):
            store = SessionStore(member_path)
            try:
                original_open = bool(store.meta.get("conversation_open"))
                original_status = str(store.meta.get("status") or "running")
                updated.append((member_path, original_open, original_status))
                store.set_conversation_open(False)
                store.update_status("completed")
                store.checkpoint()
            finally:
                store.close(update_status=False)
    except Exception:
        for member_path, original_open, original_status in reversed(updated):
            store = SessionStore(member_path)
            try:
                store.set_conversation_open(original_open)
                store.update_status(original_status)
                store.checkpoint()
            finally:
                store.close(update_status=False)
        raise


def build_open_session_rows(
    service: TerrariumService, session_dir: Path
) -> list[dict[str, Any]]:
    """Return active rows plus saved rows explicitly marked as open."""
    session_dir = Path(session_dir)
    live_rows, live_paths, live_conversation_ids = _live_rows(service)
    used_ids = {str(row["id"]) for row in live_rows}
    groups = cluster_groups(service)
    live_saved_names: set[str] = set()
    for row in live_rows:
        runtime_id = str(row.get("runtime_id") or "")
        if runtime_id:
            live_saved_names.update(groups.get(runtime_id, {runtime_id}))
        saved_name = str(row.get("saved_name") or "")
        if saved_name:
            live_saved_names.add(saved_name)

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
        if saved_name in live_saved_names:
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
        (
            item
            for item in rows
            if (item.get("conversation_id") or item.get("id")) == conversation_id
        ),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="open conversation not found")

    saved_name = str(row.get("saved_name") or "")
    path = (
        resolve_session_path_in(saved_name, session_dir=session_dir)
        if saved_name
        else None
    )
    coordination_key = (
        await asyncio.to_thread(session_coordination_key, path, session_dir)
        if path is not None
        else f"{_path_key(session_dir)}:conversation:{conversation_id}"
    )

    async def _end() -> dict[str, str]:
        runtime_id = row.get("runtime_id")
        if runtime_id:
            await lifecycle.end_session(service, str(runtime_id))
            return {"status": "ended", "conversation_id": conversation_id}

        saved_name = str(row.get("saved_name") or "")
        path = resolve_session_path_in(saved_name, session_dir=session_dir)
        if path is None:
            raise HTTPException(status_code=404, detail="saved conversation not found")
        await asyncio.to_thread(_end_saved_conversation, path, session_dir)
        index = get_session_index_default(session_dir=session_dir)
        await asyncio.to_thread(reconcile, index, session_dir=session_dir)
        return {"status": "ended", "conversation_id": conversation_id}

    try:
        return await resume_coordinator.run(
            coordination_key,
            _end,
            intent=f"end:{conversation_id}",
        )
    except RuntimeError as exc:
        if "conflicting resume request" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise


@router.get("/open")
async def list_open_sessions(
    session_dir: Path = Depends(resolve_request_session_dir),
    service: TerrariumService = Depends(get_service),
):
    """Return conversations that are live or not explicitly ended by the user."""
    return await run_in_persistence_executor(
        build_open_session_rows, service, session_dir
    )
