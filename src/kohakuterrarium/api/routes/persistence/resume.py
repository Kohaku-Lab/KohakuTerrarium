"""Adopt a saved session locally or on a selected Laboratory worker.

The route returns the legacy instance fields plus a full ``Session`` handle.
"""

import asyncio
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service, resolve_request_session_dir
from kohakuterrarium.api.routes.persistence.cluster_resume_compensation import (
    rollback_cluster_resume,
)
from kohakuterrarium.api.routes.persistence.resume_coordinator import (
    resume_coordinator,
    session_coordination_key,
)
from kohakuterrarium.api.routes.persistence.remote_resume_transfer import (
    push_and_resume_member as _push_and_resume_member,
)
from kohakuterrarium.api.routes.persistence.remote_resume_transfer import (
    worker_absolute_fallback,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.resume import resume_session as studio_resume
from kohakuterrarium.studio.persistence.store import resolve_session_path_in
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem
from kohakuterrarium.studio.sessions.handles import Session
from kohakuterrarium.studio.sessions.lifecycle import now_iso, register_session_meta
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


def _worker_absolute_for(rel: str) -> str:
    """Compatibility alias retained for route-level callers and tests."""
    return worker_absolute_fallback(rel)


class ClusterMember(BaseModel):
    """One member of a cluster session for ``ResumeRequest.members``."""

    sid: str
    on_node: str


class ResumeRequest(BaseModel):
    """Optional target, cluster membership, and working-directory overrides.

    An omitted body targets ``"_host"`` for compatibility. Cluster resumes
    require one ``(sid, on_node)`` pair per member so each worker adopts its
    own store before the host relinks them. Persisted ``cluster_members``
    metadata supplies the list when the caller does not.
    """

    on_node: str = "_host"
    members: list[ClusterMember] | None = None
    # The override applies to every rebuilt creature before it starts.
    pwd: str | None = None


@router.post("/{session_name}/resume")
async def resume_session(
    session_name: str,
    request: Request,
    req: ResumeRequest | None = None,
    session_dir: Path = Depends(resolve_request_session_dir),
    service: TerrariumService = Depends(get_service),
):
    """Share one canonical in-flight resume and reject conflicting intents."""
    body = req or ResumeRequest()
    _reject_lab_host_target(body.on_node or "_host", service)
    path = resolve_session_path_in(session_name, session_dir=session_dir)
    if path is None:
        raise HTTPException(
            status_code=404, detail=f"Session {session_name!r} not found"
        )
    key = await asyncio.to_thread(session_coordination_key, path, session_dir)
    members = tuple(
        sorted((member.sid, member.on_node) for member in (body.members or []))
    )
    intent = repr((body.on_node or "_host", body.pwd, members))
    try:
        return await resume_coordinator.run(
            key,
            lambda: _resume_session(session_name, request, body, session_dir, service),
            intent=intent,
        )
    except RuntimeError as exc:
        if "conflicting resume request" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise


async def _resume_session(
    session_name: str,
    request: Request,
    req: ResumeRequest | None,
    session_dir: Path,
    service: TerrariumService,
):
    """Resume a saved session locally or on connected worker nodes.

    Saved paths and the local service are both request-scoped so authenticated
    users cannot resolve or adopt another user's session with the same name.
    """
    on_node = (req.on_node if req is not None else "_host") or "_host"

    _reject_lab_host_target(on_node, service)

    path = await asyncio.to_thread(resolve_session_path_in, session_name, session_dir)
    if path is None:
        raise HTTPException(
            status_code=404, detail=f"Session not found: {session_name}"
        )

    if on_node == "_host":
        # The host target is valid only for a standalone service.
        try:
            session = await studio_resume(
                service, path, pwd_override=req.pwd if req is not None else None
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        instance_type = "terrarium" if len(session.creatures) > 1 else "agent"
        return {
            "instance_id": session.session_id,
            "type": instance_type,
            "session_name": session.name,
            "session": asdict(session),
        }

    # Remote adoption requires both the lab transport host and a currently
    # connected target node.
    host = getattr(service, "host", None)
    connected = (
        list(service.connected_nodes()) if hasattr(service, "connected_nodes") else []
    )
    if host is None or on_node not in connected:
        raise HTTPException(
            status_code=404,
            detail=f"on_node={on_node!r} is not a connected lab node",
        )

    # Persisted cluster membership prevents a multi-worker session from being
    # resumed as an isolated singleton when the caller omits ``members``.
    requested_members = req.members if (req is not None and req.members) else None
    saved_members = await asyncio.to_thread(_read_saved_cluster_members, path)
    cluster_members = requested_members or saved_members
    if cluster_members and len(cluster_members) > 1:
        # Validate all targets before mutating workers to avoid a partially
        # resumed cluster.
        missing = [m for m in cluster_members if m.on_node not in connected]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=(
                    "CF-6 cluster resume: not every member's worker is "
                    f"connected (missing: {[m.on_node for m in missing]!r}). "
                    "Reconnect every worker named in cluster_members and "
                    "retry."
                ),
            )
        return await _resume_cluster(
            service,
            request,
            host,
            cluster_members,
            on_node,
            session_name,
            primary_sid=normalize_session_stem(path),
            session_dir=session_dir,
            pwd_override=req.pwd if req is not None else None,
        )

    sid, meta, worker_pwd_exists, remote_session_path = await _push_and_resume_member(
        host=host,
        request=request,
        path=path,
        on_node=on_node,
        pwd_override=req.pwd if req is not None else None,
    )

    # Resumed creatures bypass spawn-time home and name cache population.
    # Refreshing the worker roster makes subsequent creature, history, and
    # chat lookups route to the correct node.
    resumed_creatures: list[dict] = []
    list_creatures = getattr(service, "list_creatures", None)
    if callable(list_creatures):
        try:
            roster = await list_creatures()
        except Exception:  # pragma: no cover - defensive
            roster = ()
        for c in roster:
            if getattr(c, "graph_id", None) != sid:
                continue
            resumed_creatures.append(
                {
                    "creature_id": c.creature_id,
                    "name": c.name,
                    "home_node": on_node,
                    "running": getattr(c, "is_running", True),
                    "is_privileged": getattr(c, "is_privileged", False),
                }
            )

    # Controller metadata is the source for remote session listings and
    # synthesized handles. Prefer the live roster identity when available.
    primary_cid = (
        resumed_creatures[0]["creature_id"]
        if resumed_creatures
        else (meta.get("agents") or [""])[0]
    )
    register_session_meta(
        service,
        sid,
        {
            "name": meta.get("terrarium_name") or meta.get("session_id") or sid,
            "config_path": meta.get("config_path", ""),
            "pwd": meta.get("pwd", ""),
            "on_node": on_node,
            "resumed_from": str(path),
            "remote_session_path": remote_session_path,
            "conversation_id": str(meta.get("conversation_id") or ""),
            "creature_id": primary_cid,
        },
    )

    name = meta.get("terrarium_name") or session_name
    # Real worker creature IDs keep chat and history addressable. Metadata
    # provides a well-formed fallback when roster discovery is unavailable.
    creatures_payload = resumed_creatures or [
        {"creature_id": agent, "name": agent} for agent in (meta.get("agents") or [])
    ]
    synthetic = Session(
        session_id=sid,
        name=name,
        creatures=creatures_payload,
        channels=[],
        has_root=bool(meta.get("terrarium_creatures")),
        pwd=meta.get("pwd", ""),
        created_at=now_iso(),
        config_path=meta.get("config_path", ""),
        home_node=on_node,
    )
    # ``Session.__post_init__`` checks the controller filesystem, so a remote
    # worker's working-directory result is authoritative when present.
    if worker_pwd_exists is not None:
        synthetic.pwd_exists = worker_pwd_exists
    instance_type = "terrarium" if (meta.get("config_type") == "terrarium") else "agent"
    return {
        "instance_id": sid,
        "type": instance_type,
        "session_name": name,
        "session": asdict(synthetic),
        "on_node": on_node,
    }


def _reject_lab_host_target(on_node: str, service: TerrariumService) -> None:
    """Reject host-local adoption before resolving any saved-session path."""
    if on_node != "_host" or not hasattr(service, "connected_nodes"):
        return
    raise HTTPException(
        status_code=400,
        detail=(
            "lab-host mode runs no agents on the host — resume on a "
            "worker node (pass on_node=<worker name>)"
        ),
    )


def _read_saved_cluster_members(path: Path) -> list[ClusterMember] | None:
    """Read valid persisted cluster membership from a saved store.

    Missing, malformed, or singleton membership returns ``None``. The blocking
    store access must run through :func:`asyncio.to_thread`.
    """
    # ``SessionStore`` creates missing files, so existence must be checked
    # before opening to preserve the later canonical 404.
    if not path.exists():
        return None
    try:
        store = SessionStore.open_readonly(path)
    except Exception:
        return None
    try:
        raw = store.meta.get("cluster_members")
    finally:
        store.close(update_status=False)
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    members: list[ClusterMember] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("sid")
        node = entry.get("on_node")
        if isinstance(sid, str) and sid and isinstance(node, str) and node:
            members.append(ClusterMember(sid=sid, on_node=node))
    if len(members) < 2:
        return None
    return members


async def _resume_cluster(
    service: TerrariumService,
    request: Request,
    host,
    members: list[ClusterMember],
    primary_on_node: str,
    session_name: str,
    *,
    primary_sid: str,
    session_dir: Path,
    pwd_override: str | None = None,
) -> dict:
    """Resume all cluster members on their workers and restore their links.

    Store paths and worker connectivity are validated before mutation. The
    refreshed roster supplies authoritative creature IDs for routing and
    relinking, while the response represents the primary member.
    """
    member_ids = [member.sid for member in members]
    if len(set(member_ids)) != len(member_ids):
        raise HTTPException(
            status_code=400,
            detail="cluster resume members must use unique session ids",
        )
    primary_matches = [member for member in members if member.sid == primary_sid]
    if len(primary_matches) != 1:
        raise HTTPException(
            status_code=400,
            detail="cluster resume members must contain the requested primary session",
        )
    primary_member = primary_matches[0]
    if primary_member.on_node != primary_on_node:
        raise HTTPException(
            status_code=400,
            detail="cluster primary worker does not match on_node",
        )

    # Resolve every store before mutating workers so a missing mirror cannot
    # leave a partially resumed cluster.
    paths: dict[str, Path] = {}
    for m in members:
        resolved = await asyncio.to_thread(resolve_session_path_in, m.sid, session_dir)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=f"CF-6 cluster resume: no saved store for member sid={m.sid!r}",
            )
        paths[m.sid] = resolved

    # Primary-first ordering makes its metadata the canonical response source.
    ordered: list[ClusterMember] = [primary_member] + [
        m for m in members if m.sid != primary_member.sid
    ]
    resumed: dict[str, tuple[str, dict, str]] = {}
    remote_paths: dict[str, str] = {}
    registered_session_ids: list[str] = []
    connected_pairs: list[tuple[str, str]] = []
    try:
        for m in ordered:
            (
                new_sid,
                new_meta,
                _member_pwd_exists,
                remote_session_path,
            ) = await _push_and_resume_member(
                host=host,
                request=request,
                path=paths[m.sid],
                on_node=m.on_node,
                pwd_override=pwd_override,
            )
            resumed[m.sid] = (new_sid, new_meta, m.on_node)
            remote_paths[m.sid] = remote_session_path
        new_session_ids = [new_sid for new_sid, _meta, _node in resumed.values()]
        if len(set(new_session_ids)) != len(new_session_ids):
            raise RuntimeError("workers returned duplicate resumed session ids")
    except Exception as exc:
        rollback_errors = await rollback_cluster_resume(
            service,
            resumed,
            registered_session_ids,
            connected_pairs,
        )
        detail = f"cluster member resume failed: {exc}"
        if rollback_errors:
            detail += "; rollback errors: " + "; ".join(rollback_errors)
        raise HTTPException(status_code=502, detail=detail) from exc

    # Roster refresh replaces stale pre-stop routing identities before relink.
    new_creature_by_member: dict[str, str] = {}
    list_creatures = getattr(service, "list_creatures", None)
    roster: tuple = ()
    if callable(list_creatures):
        try:
            roster = tuple(await list_creatures())
        except Exception:  # pragma: no cover - defensive
            roster = ()
    for original_sid, (new_sid, _meta, _node) in resumed.items():
        for c in roster:
            if getattr(c, "graph_id", None) == new_sid:
                new_creature_by_member[original_sid] = c.creature_id
                break

    # Register every adopted member before relinking so metadata-backed
    # lookups observe a complete cluster.
    try:
        for original_sid, (new_sid, new_meta, node) in resumed.items():
            creature_id = new_creature_by_member.get(original_sid) or (
                (new_meta.get("agents") or [""])[0]
            )
            register_session_meta(
                service,
                new_sid,
                {
                    "name": new_meta.get("terrarium_name")
                    or new_meta.get("session_id")
                    or new_sid,
                    "config_path": new_meta.get("config_path", ""),
                    "pwd": new_meta.get("pwd", ""),
                    "on_node": node,
                    "resumed_from": str(paths[original_sid]),
                    "remote_session_path": remote_paths[original_sid],
                    "conversation_id": str(new_meta.get("conversation_id") or ""),
                    "creature_id": creature_id,
                },
            )
            registered_session_ids.append(new_sid)
    except Exception as exc:
        rollback_errors = await rollback_cluster_resume(
            service,
            resumed,
            registered_session_ids,
            connected_pairs,
        )
        detail = f"cluster registration failed: {exc}"
        if rollback_errors:
            detail += "; rollback errors: " + "; ".join(rollback_errors)
        raise HTTPException(status_code=502, detail=detail) from exc

    # Default-channel connections rebuild cluster links from the primary.
    primary_cid = new_creature_by_member.get(primary_member.sid)
    try:
        if not primary_cid:
            raise RuntimeError("resumed primary creature is missing from the roster")
        for m in ordered[1:]:
            peer_cid = new_creature_by_member.get(m.sid)
            if not peer_cid:
                raise RuntimeError(
                    f"resumed member {m.sid!r} is missing from the roster"
                )
            await service.connect(primary_cid, peer_cid)
            connected_pairs.append((primary_cid, peer_cid))
    except Exception as exc:
        rollback_errors = await rollback_cluster_resume(
            service,
            resumed,
            registered_session_ids,
            connected_pairs,
        )
        detail = f"cluster relink failed: {exc}"
        if rollback_errors:
            detail += "; rollback errors: " + "; ".join(rollback_errors)
        raise HTTPException(status_code=502, detail=detail) from exc

    primary_new_sid, primary_meta, _ = resumed[primary_member.sid]
    name = primary_meta.get("terrarium_name") or session_name
    creatures_payload: list[dict] = []
    for c in roster:
        if getattr(c, "graph_id", None) != primary_new_sid:
            continue
        creatures_payload.append(
            {
                "creature_id": c.creature_id,
                "name": c.name,
                "home_node": primary_member.on_node,
                "running": getattr(c, "is_running", True),
                "is_privileged": getattr(c, "is_privileged", False),
            }
        )
    if not creatures_payload:
        creatures_payload = [
            {"creature_id": agent, "name": agent}
            for agent in (primary_meta.get("agents") or [])
        ]
    synthetic = Session(
        session_id=primary_new_sid,
        name=name,
        creatures=creatures_payload,
        channels=[],
        has_root=bool(primary_meta.get("terrarium_creatures")),
        pwd=primary_meta.get("pwd", ""),
        created_at=now_iso(),
        config_path=primary_meta.get("config_path", ""),
        home_node=primary_member.on_node,
    )
    instance_type = (
        "terrarium" if (primary_meta.get("config_type") == "terrarium") else "agent"
    )
    return {
        "instance_id": primary_new_sid,
        "type": instance_type,
        "session_name": name,
        "session": asdict(synthetic),
        "on_node": primary_on_node,
        "cluster_members": [
            {"sid": new_sid, "on_node": node}
            for (new_sid, _meta, node) in resumed.values()
        ],
    }
