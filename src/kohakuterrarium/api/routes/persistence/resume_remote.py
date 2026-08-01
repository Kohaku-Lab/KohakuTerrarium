"""Remote workspace preflight and controller-mirror persistence helpers."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index import (
    SessionIndexEntry,
    get_session_index_default,
)
from kohakuterrarium.studio.persistence.session_index.hooks import push_index_update
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY


def _error_message(error: dict | str, fallback: str) -> str:
    if isinstance(error, dict):
        return error.get("message") or fallback
    return str(error or fallback)


_MISSING = object()
_MIRROR_KEYS = (
    "live_graph_manifest",
    "pwd",
    "on_node",
    "cluster_members",
    "conversation_open",
    "status",
    "last_active",
)


@dataclass(frozen=True)
class RemoteMirrorSnapshot:
    path: Path
    meta: dict[str, Any]
    index_row: dict[str, Any] | None


class RemoteMirrorRollbackError(RuntimeError):
    """Controller mirror rollback could not be made durable."""


def persist_remote_workspace_meta(
    path: Path,
    worker_meta: dict[str, Any],
    on_node: str,
    *,
    cluster_members: list[dict[str, str]] | None = None,
) -> RemoteMirrorSnapshot:
    """Publish worker workspace/lifecycle state to its controller mirror."""
    updates = {
        key: worker_meta[key]
        for key in (
            "live_graph_manifest",
            "pwd",
            "conversation_open",
            "status",
            "last_active",
        )
        if key in worker_meta
    }
    updates["on_node"] = on_node
    if cluster_members is not None:
        updates["cluster_members"] = cluster_members
    index = get_session_index_default(path.parent)
    original_meta = read_session_meta(path)
    snapshot = RemoteMirrorSnapshot(
        path=path,
        meta={key: original_meta.get(key, _MISSING) for key in _MIRROR_KEYS},
        index_row=index.get(path.name),
    )
    store: SessionStore | None = SessionStore(path, writer_lock=True)
    try:
        store.meta.update(updates)
        store.checkpoint()
        if push_index_update(store, index) is None:
            raise RuntimeError("session index update failed")
        store.close(update_status=False)
        store = None
    except BaseException:
        if store is not None:
            try:
                store.close(update_status=False)
            except BaseException:
                pass
        rollback_remote_workspace_meta(snapshot)
        raise
    return snapshot


def rollback_remote_workspace_meta(snapshot: RemoteMirrorSnapshot) -> None:
    """Restore the exact mirror metadata and derived index row."""
    index = get_session_index_default(snapshot.path.parent)
    store: SessionStore | None = None
    try:
        store = SessionStore(snapshot.path, writer_lock=True)
        for key, value in snapshot.meta.items():
            if value is _MISSING:
                store.meta.pop(key, None)
            else:
                store.meta[key] = value
        store.checkpoint()
        if snapshot.index_row is None:
            index.delete(snapshot.path.name)
        else:
            index.upsert(SessionIndexEntry(**snapshot.index_row))
        store.close(update_status=False)
        store = None
    except BaseException as exc:
        if store is not None:
            try:
                store.close(update_status=False)
            except BaseException:
                pass
        raise RemoteMirrorRollbackError(str(exc)) from exc


async def worker_workspace_preflight(
    host,
    path: Path,
    on_node: str,
    *,
    replacements: dict[str, str] | None = None,
    pwd_override: str | None = None,
    require_ready: bool = True,
) -> dict[str, Any]:
    """Validate saved workspaces on their execution node."""
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Session file not found")
    try:
        meta = await asyncio.to_thread(read_session_meta, path)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Unable to read session workspace metadata: {exc}",
        ) from exc
    resume_state = meta.get("workspace_resume_state")
    if isinstance(resume_state, dict) and resume_state.get("status") == "partial_dirty":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "partial_dirty",
                "message": "Session has an incomplete workspace rollback",
            },
        )
    raw_manifest = meta.get(MANIFEST_KEY)
    body = (
        {
            "manifest": raw_manifest,
            "workspace_overrides": replacements,
            "pwd_override": pwd_override,
        }
        if raw_manifest is not None
        else {"legacy_pwd": meta.get("pwd"), "pwd_override": pwd_override}
    )
    response = await host.request(
        to_node=on_node,
        namespace="terrarium.session",
        type="workspace_preflight",
        body=body,
        timeout=30.0,
    )
    if isinstance(response, dict) and "error" in response:
        raise HTTPException(
            status_code=422,
            detail=_error_message(response["error"], "Workspace preflight failed"),
        )
    result = response or {}
    planning_error = result.get("error")
    if planning_error:
        raise HTTPException(status_code=422, detail=planning_error)
    if require_ready and not result.get("ready", False):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "workspace_replacement_required",
                "on_node": on_node,
                **result,
            },
        )
    return result


__all__ = [
    "RemoteMirrorRollbackError",
    "RemoteMirrorSnapshot",
    "persist_remote_workspace_meta",
    "rollback_remote_workspace_meta",
    "worker_workspace_preflight",
]
