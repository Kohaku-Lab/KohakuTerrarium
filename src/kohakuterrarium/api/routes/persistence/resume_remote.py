"""Remote workspace preflight and session transfer helpers."""

import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import HTTPException, Request

from kohakuterrarium.laboratory.adapters.file_scopes import kt_config_home
from kohakuterrarium.laboratory.file_transfer import stream_write_file
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index import (
    SessionIndexEntry,
    get_session_index_default,
)
from kohakuterrarium.studio.persistence.session_index.hooks import push_index_update
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem
from kohakuterrarium.studio.sessions.handles import Session
from kohakuterrarium.studio.sessions.lifecycle import now_iso
from kohakuterrarium.studio.sessions.registry import meta_for, register_session_meta
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


async def build_remote_response(
    service,
    *,
    sid: str,
    meta: dict,
    on_node: str,
    path: Path,
    session_name: str,
    worker_pwd_exists: bool,
) -> dict:
    """Register a remote runtime and build its public resume response."""
    list_creatures = getattr(service, "list_creatures", None)
    try:
        roster = await list_creatures() if callable(list_creatures) else ()
    except Exception:
        roster = ()
    resumed_creatures = [
        {
            "creature_id": creature.creature_id,
            "name": creature.name,
            "home_node": on_node,
            "running": getattr(creature, "is_running", True),
            "is_privileged": getattr(creature, "is_privileged", False),
        }
        for creature in roster
        if getattr(creature, "graph_id", None) == sid
    ]
    fallback_agents = [
        {"creature_id": agent, "name": agent}
        for agent in (meta.get("agents") or [])
        if isinstance(agent, str) and agent
    ]
    creatures = resumed_creatures or fallback_agents
    creature_id = creatures[0]["creature_id"] if creatures else None
    home = getattr(service, "_home", None)
    previous_home = (
        home.get(creature_id, _MISSING)
        if creature_id is not None and isinstance(home, dict)
        else _MISSING
    )
    registry = meta_for(service)
    previous_meta = registry.get(sid, _MISSING)
    try:
        if creature_id is not None and isinstance(home, dict):
            home.setdefault(creature_id, on_node)
        register_session_meta(
            service,
            sid,
            {
                "type": meta.get("config_type", "agent"),
                "name": meta.get("terrarium_name") or meta.get("session_id") or sid,
                "config_path": meta.get("config_path", ""),
                "pwd": meta.get("pwd", ""),
                "on_node": on_node,
                "resumed_from": str(path),
                "creature_id": creature_id,
            },
        )
        synthetic = Session(
            session_id=sid,
            name=meta.get("terrarium_name") or session_name,
            creatures=creatures,
            channels=[],
            has_root=bool(meta.get("terrarium_creatures")),
            pwd=meta.get("pwd", ""),
            created_at=now_iso(),
            config_path=meta.get("config_path", ""),
            home_node=on_node,
        )
        if worker_pwd_exists is not None:
            synthetic.pwd_exists = worker_pwd_exists
        return {
            "instance_id": sid,
            "type": (
                "terrarium" if meta.get("config_type") == "terrarium" else "agent"
            ),
            "session_name": synthetic.name,
            "session": asdict(synthetic),
            "on_node": on_node,
        }
    except BaseException:
        if previous_meta is _MISSING:
            registry.pop(sid, None)
        else:
            registry[sid] = previous_meta
        if creature_id is not None and isinstance(home, dict):
            if previous_home is _MISSING:
                home.pop(creature_id, None)
            else:
                home[creature_id] = previous_home
        raise


async def register_cluster_members(
    service, resumed, paths
) -> tuple[dict[str, str], tuple[Any, ...]]:
    """Publish controller lifecycle metadata after all adoptions succeed."""
    roster = tuple(await service.list_creatures())
    creatures: dict[str, str] = {}
    for original_sid, (new_sid, new_meta, node) in resumed.items():
        creature_id = next(
            (
                creature.creature_id
                for creature in roster
                if getattr(creature, "graph_id", None) == new_sid
                and getattr(creature, "node_id", node) == node
            ),
            None,
        )
        if creature_id is not None:
            home = getattr(service, "_home", None)
            if isinstance(home, dict):
                home.setdefault(creature_id, node)
            creatures[original_sid] = creature_id
        meta_for(service)[new_sid] = {
            "type": new_meta.get("config_type", "agent"),
            "name": new_meta.get("terrarium_name")
            or new_meta.get("session_id")
            or new_sid,
            "config_path": new_meta.get("config_path", ""),
            "pwd": new_meta.get("pwd", ""),
            "on_node": node,
            "resumed_from": str(paths[original_sid]),
            "creature_id": creature_id,
        }
    return creatures, roster


def unregister_cluster_members(service, resumed, registered_creatures) -> None:
    """Remove controller registry state created for a failed cluster resume."""
    meta = meta_for(service)
    for _original_sid, (new_sid, _new_meta, _node) in resumed.items():
        meta.pop(new_sid, None)
    home = getattr(service, "_home", None)
    if isinstance(home, dict):
        for creature_id in registered_creatures.values():
            home.pop(creature_id, None)


def build_cluster_response(
    *,
    primary_new_sid: str,
    primary_meta: dict[str, Any],
    primary_node: str,
    resumed_roster: tuple[Any, ...],
    resumed: dict[str, tuple[str, dict, str]],
    ordered,
    session_name: str,
    primary_on_node: str,
) -> dict[str, Any]:
    """Build the public response for an atomically restored remote cluster."""
    metadata = dict(primary_meta)
    metadata["cluster_members"] = [
        {"sid": resumed[item.sid][0], "on_node": item.on_node} for item in ordered
    ]
    creatures = [
        {
            "creature_id": creature.creature_id,
            "name": creature.name,
            "home_node": primary_node,
            "running": getattr(creature, "is_running", True),
            "is_privileged": getattr(creature, "is_privileged", False),
        }
        for creature in resumed_roster
        if getattr(creature, "graph_id", None) == primary_new_sid
    ]
    if not creatures:
        creatures = [
            {"creature_id": agent, "name": agent}
            for agent in (metadata.get("agents") or [])
            if isinstance(agent, str) and agent
        ]
    name = metadata.get("terrarium_name") or session_name
    synthetic = Session(
        session_id=primary_new_sid,
        name=name,
        creatures=creatures,
        channels=[],
        has_root=bool(metadata.get("terrarium_creatures")),
        pwd=metadata.get("pwd", ""),
        created_at=now_iso(),
        config_path=metadata.get("config_path", ""),
        home_node=primary_node,
    )
    return {
        "instance_id": primary_new_sid,
        "type": (
            "terrarium" if metadata.get("config_type") == "terrarium" else "agent"
        ),
        "session_name": name,
        "session": asdict(synthetic),
        "on_node": primary_on_node,
        "cluster_members": metadata["cluster_members"],
    }


async def cleanup_remote_sessions(host, resumed: list[tuple[str, str]]) -> list[str]:
    """Best-effort cleanup for already-adopted worker graphs."""
    failures = []
    for on_node, session_id in reversed(resumed):
        try:
            response = await host.request(
                to_node=on_node,
                namespace="terrarium.session",
                type="remove",
                body={"session_id": session_id},
                timeout=30.0,
            )
            if isinstance(response, dict) and "error" in response:
                failures.append(f"{on_node}:{session_id}")
        except Exception:
            failures.append(f"{on_node}:{session_id}")
    return failures


def worker_absolute_for(rel: str) -> str:
    return str(kt_config_home() / rel)


async def push_and_resume_member(
    *,
    host,
    request: Request,
    path: Path,
    on_node: str,
    pwd_override: str | None = None,
    workspace_overrides: dict[str, str] | None = None,
) -> tuple[str, dict, bool | None]:
    """Transfer one store and invoke its worker resume RPC."""
    mirror = getattr(request.app.state, "session_mirror", None)
    if mirror is not None and hasattr(mirror, "checkpoint"):
        try:
            mirror.checkpoint(normalize_session_stem(path))
        except Exception:
            pass
    try:
        async with aiofiles.open(path, "rb") as file:
            data = await file.read()
    except OSError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rel = f"resume/{path.name}"
    try:
        await stream_write_file(host, on_node, "config://", rel, data)
        stat = await host.request(
            to_node=on_node,
            namespace="terrarium.files",
            type="stat",
            body={"scope": "config://", "path": rel},
            timeout=10.0,
        )
        if isinstance(stat, dict) and "error" in stat:
            raise HTTPException(
                status_code=502, detail=_error_message(stat["error"], "Transfer failed")
            )
        response = await host.request(
            to_node=on_node,
            namespace="terrarium.session",
            type="resume",
            body={
                "scope": "config://",
                "rel": rel,
                # Retained for compatibility with older workers. Updated
                # workers resolve scope+rel against their own config root.
                "path": worker_absolute_for(rel),
                "pwd_override": pwd_override,
                "workspace_overrides": workspace_overrides,
            },
            timeout=60.0,
        )
        if isinstance(response, dict) and "error" in response:
            raise HTTPException(
                status_code=502,
                detail=_error_message(response["error"], "Worker resume failed"),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to resume on worker {on_node!r}: {exc}"
        ) from exc
    sid = response.get("session_id", "")
    meta = response.get("meta", {}) or {}
    if not isinstance(sid, str) or not sid:
        raise HTTPException(
            status_code=502, detail=f"worker {on_node!r} returned no session_id"
        )
    pwd_exists = response.get("pwd_exists")
    return sid, meta, pwd_exists if isinstance(pwd_exists, bool) else None
