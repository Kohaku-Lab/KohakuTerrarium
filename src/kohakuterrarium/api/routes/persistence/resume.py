"""Adopt a saved session locally or on a selected Laboratory worker.

The route returns the legacy instance fields plus a full ``Session`` handle.
"""

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from kohakuterrarium.api.deps import (
    get_service_factory,
    resolve_request_session_dir,
)
from kohakuterrarium.api.routes.persistence.cluster_resume_compensation import (
    rollback_cluster_resume,
    snapshot_controller_state,
)
from kohakuterrarium.api.routes.persistence.resume_coordinator import (
    resume_coordinator,
    session_coordination_key,
)
from kohakuterrarium.api.routes.persistence.remote_resume_transfer import (
    push_and_resume_member as _push_and_resume_member,
)
from kohakuterrarium.api.routes.persistence.resume_remote import (
    RemoteMirrorRollbackError,
    persist_remote_workspace_meta as _persist_remote_workspace_meta,
    worker_workspace_preflight as _worker_workspace_preflight,
)
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.studio.persistence.resume import resume_session as studio_resume
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY, parse_manifest
from kohakuterrarium.terrarium.workspace_resume import (
    WorkspaceResumeError,
    plan_workspace_resume,
    preflight_session_workspaces,
    preflight_to_dict,
    preflight_workspace_resume,
)
from kohakuterrarium.studio.persistence.store import resolve_session_path_in
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem
from kohakuterrarium.studio.sessions.handles import Session
from kohakuterrarium.studio.sessions.lifecycle import now_iso, register_session_meta
from kohakuterrarium.terrarium.resume import prepare_resume_workspace
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class ClusterMember(BaseModel):
    """One member of a cluster session."""

    sid: str
    on_node: str


class WorkspacePreflightRequest(BaseModel):
    """Optional execution node and candidate workspace replacements."""

    on_node: str = "_host"
    members: list[ClusterMember] | None = None
    pwd: str | None = None
    workspace_overrides: dict[str, str] | None = None
    member_workspace_overrides: dict[str, dict[str, str]] | None = None
    member_pwd_overrides: dict[str, str] | None = None


@router.post("/{session_name}/resume/preflight")
async def preflight_resume(
    session_name: str,
    req: WorkspacePreflightRequest | None = None,
    session_dir: Path = Depends(resolve_request_session_dir),
    service_factory: Callable[[], TerrariumService] = Depends(get_service_factory),
) -> dict[str, Any]:
    """Inspect local session workspaces without creating a runtime."""
    if req is not None and req.pwd is not None and req.workspace_overrides:
        raise HTTPException(
            status_code=422,
            detail="pwd and workspace_overrides are mutually exclusive",
        )
    path = await asyncio.to_thread(resolve_session_path_in, session_name, session_dir)
    if path is None:
        raise HTTPException(status_code=404, detail="Session not found")
    requested_members = req.members if req is not None and req.members else None
    saved_members = await asyncio.to_thread(_read_saved_cluster_members, path)
    primary_sid = await asyncio.to_thread(
        _read_saved_session_id, path
    ) or normalize_session_stem(path)
    _validate_cluster_member_selection(
        requested_members,
        saved_members,
        primary_sid=primary_sid,
        primary_on_node=(
            req.on_node
            if req is not None and "on_node" in req.model_fields_set
            else None
        ),
    )
    cluster_members = requested_members or saved_members
    if cluster_members:
        if req is not None and req.workspace_overrides and len(cluster_members) > 1:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Cluster workspace overrides must be scoped by member session ID"
                ),
            )
        service = service_factory()
        host = getattr(service, "host", None)
        if host is None:
            raise HTTPException(status_code=503, detail="Lab host not configured")
        results = []
        for member in cluster_members:
            member_path = await asyncio.to_thread(
                resolve_session_path_in, member.sid, session_dir
            )
            if member_path is None:
                raise HTTPException(
                    status_code=404, detail=f"Session {member.sid!r} not found"
                )
            replacements = (req.member_workspace_overrides or {}).get(member.sid)
            member_pwd = (req.member_pwd_overrides or {}).get(member.sid)
            result = await _worker_workspace_preflight(
                host,
                member_path,
                member.on_node,
                replacements=replacements,
                pwd_override=member_pwd or req.pwd,
                require_ready=False,
            )
            results.append({"sid": member.sid, "on_node": member.on_node, **result})
        return {
            "legacy": False,
            "ready": all(item.get("ready", False) for item in results),
            "members": results,
            "gaps": [],
        }
    on_node = req.on_node if req is not None else "_host"
    if on_node != "_host":
        service = service_factory()
        host = getattr(service, "host", None)
        if host is None:
            raise HTTPException(status_code=503, detail="Lab host not configured")
        return await _worker_workspace_preflight(
            host,
            path,
            on_node,
            replacements=(req.workspace_overrides if req else None),
            pwd_override=(req.pwd if req else None),
            require_ready=False,
        )
    try:
        preflight = await asyncio.to_thread(preflight_session_workspaces, path)
    except WorkspaceResumeError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code.value, "message": str(exc)},
        ) from exc
    if preflight is None:
        meta = await asyncio.to_thread(read_session_meta, path)
        saved_pwd = meta.get("pwd")
        effective_pwd = (req.pwd if req is not None else None) or saved_pwd
        ready = bool(effective_pwd and Path(effective_pwd).is_dir())
        return {
            "legacy": True,
            "ready": ready,
            "members": [],
            "gaps": (
                []
                if ready
                else [
                    {
                        "gap_id": "legacy",
                        "saved_pwd": saved_pwd,
                        "status": "invalid" if saved_pwd else "missing",
                        "creature_ids": [],
                    }
                ]
            ),
        }
    replacements = req.workspace_overrides if req is not None else None
    pwd_override = req.pwd if req is not None else None
    if pwd_override is not None:
        replacements = {
            member.creature_id: pwd_override for member in preflight.members
        }
    if replacements:
        raw_manifest = (await asyncio.to_thread(read_session_meta, path)).get(
            MANIFEST_KEY
        )
        assert isinstance(raw_manifest, dict)
        manifest = parse_manifest(raw_manifest)
        try:
            plan = plan_workspace_resume(
                manifest,
                replacements,
                allow_valid_targets=pwd_override is not None,
            )
        except WorkspaceResumeError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": exc.code.value, "message": str(exc)},
            ) from exc
        preflight = preflight_workspace_resume(plan.manifest)
    return {"legacy": False, **preflight_to_dict(preflight)}


class ResumeRequest(BaseModel):
    """Optional target, cluster membership, and working-directory overrides.

    An omitted body targets ``"_host"`` for compatibility. Cluster resumes
    require one ``(sid, on_node)`` pair per member so each worker adopts its
    own store before the host relinks them. Persisted ``cluster_members``
    metadata supplies the list when the caller does not.
    """

    on_node: str = "_host"
    members: list[ClusterMember] | None = None
    # Explicit compatibility override: broadcast one directory to the team.
    pwd: str | None = None
    # Preferred modern form: only named creatures/path groups are replaced.
    workspace_overrides: dict[str, str] | None = None
    # Cluster form: member session id -> that node's targeted replacements.
    member_workspace_overrides: dict[str, dict[str, str]] | None = None
    member_pwd_overrides: dict[str, str] | None = None


@router.post("/{session_name}/resume")
async def resume_session(
    session_name: str,
    request: Request,
    req: ResumeRequest | None = None,
    session_dir: Path = Depends(resolve_request_session_dir),
    service_factory: Callable[[], TerrariumService] = Depends(get_service_factory),
):
    """Share one canonical in-flight resume and reject conflicting intents."""
    body = req or ResumeRequest()
    if body.pwd is not None and body.workspace_overrides:
        raise HTTPException(
            status_code=422,
            detail="pwd and workspace_overrides are mutually exclusive",
        )
    on_node = body.on_node or "_host"
    _reject_lab_host_target(request, on_node)
    path = await asyncio.to_thread(resolve_session_path_in, session_name, session_dir)
    if path is None:
        raise HTTPException(
            status_code=404, detail=f"Session {session_name!r} not found"
        )
    key = await asyncio.to_thread(session_coordination_key, path, session_dir)
    intent = _resume_intent(body)
    try:
        return await resume_coordinator.run(
            key,
            lambda: _resume_session(
                session_name,
                request,
                body,
                path,
                session_dir,
                service_factory,
            ),
            intent=intent,
        )
    except RuntimeError as exc:
        if "conflicting resume request" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise


async def _resume_session(
    session_name: str,
    request: Request,
    req: ResumeRequest,
    path: Path,
    session_dir: Path,
    service_factory: Callable[[], TerrariumService],
):
    """Resume a saved session locally or on connected worker nodes.

    Local workspace validation finishes before the lazy service factory is
    called, so invalid resumes cannot allocate an engine runtime.
    """
    on_node = req.on_node or "_host"

    if on_node == "_host":
        try:
            await asyncio.to_thread(
                prepare_resume_workspace,
                path,
                pwd=req.pwd,
                workspace_overrides=req.workspace_overrides,
            )
        except WorkspaceResumeError as exc:
            status = 409 if exc.code.value == "unresolved" else 422
            raise HTTPException(
                status_code=status,
                detail={"code": exc.code.value, "message": str(exc)},
            ) from exc
        except SessionNotResumableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service = service_factory()
        if getattr(service, "host", None) is not None or hasattr(
            service, "connected_nodes"
        ):
            raise HTTPException(
                status_code=400,
                detail="Laboratory hosts cannot resume agents on _host; choose a worker",
            )
        try:
            session = await studio_resume(
                service,
                path,
                pwd_override=req.pwd,
                workspace_overrides=req.workspace_overrides,
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

    service = service_factory()
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
    requested_members = req.members or None
    saved_members = await asyncio.to_thread(_read_saved_cluster_members, path)
    primary_sid = await asyncio.to_thread(
        _read_saved_session_id, path
    ) or normalize_session_stem(path)
    _validate_cluster_member_selection(
        requested_members,
        saved_members,
        primary_sid=primary_sid,
        primary_on_node=on_node,
    )
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
            primary_sid=primary_sid,
            session_dir=session_dir,
            pwd_override=req.pwd,
            workspace_overrides=req.workspace_overrides,
            member_workspace_overrides=req.member_workspace_overrides,
            member_pwd_overrides=req.member_pwd_overrides,
        )

    await _worker_workspace_preflight(
        host,
        path,
        on_node,
        replacements=req.workspace_overrides,
        pwd_override=req.pwd,
    )
    (
        sid,
        meta,
        worker_pwd_exists,
        remote_session_path,
        worker_creatures,
    ) = await _push_and_resume_member(
        host=host,
        request=request,
        path=path,
        on_node=on_node,
        pwd_override=req.pwd,
        workspace_overrides=req.workspace_overrides,
    )
    try:
        mirror_snapshot = await asyncio.to_thread(
            _persist_remote_workspace_meta, path, meta, on_node
        )
    except BaseException as exc:
        rollback_errors = await rollback_cluster_resume(
            service,
            {"single": (sid, meta, on_node)},
            [],
            [],
        )
        if rollback_errors or isinstance(exc, RemoteMirrorRollbackError):
            raise _partial_dirty(exc, rollback_errors) from exc
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Resumed creatures bypass spawn-time home and name cache population.
    # Refreshing the worker roster makes subsequent creature, history, and
    # chat lookups route to the correct node.
    resumed_creatures: list[dict] = [
        {**creature, "home_node": on_node} for creature in worker_creatures
    ]
    list_creatures = getattr(service, "list_creatures", None)
    if callable(list_creatures):
        try:
            roster = await list_creatures()
        except Exception:  # pragma: no cover - defensive
            roster = ()
        refreshed_creatures: list[dict] = []
        for c in roster:
            if getattr(c, "graph_id", None) != sid:
                continue
            refreshed_creatures.append(
                {
                    "creature_id": c.creature_id,
                    "name": c.name,
                    "home_node": on_node,
                    "running": getattr(c, "is_running", True),
                    "is_privileged": getattr(c, "is_privileged", False),
                }
            )
        if refreshed_creatures:
            resumed_creatures = refreshed_creatures

    response_creature_ids = [
        str(creature["creature_id"])
        for creature in resumed_creatures
        if creature.get("creature_id")
    ]
    if not response_creature_ids:
        response_creature_ids = [
            str(agent) for agent in (meta.get("agents") or []) if agent
        ]
    controller_snapshot = snapshot_controller_state(
        service,
        sid,
        response_creature_ids,
    )
    try:
        return _build_single_remote_response(
            service,
            sid=sid,
            meta=meta,
            on_node=on_node,
            path=path,
            session_name=session_name,
            worker_pwd_exists=worker_pwd_exists,
            remote_session_path=remote_session_path,
            resumed_creatures=resumed_creatures,
        )
    except BaseException as exc:
        rollback_errors = await rollback_cluster_resume(
            service,
            {"single": (sid, meta, on_node)},
            [sid],
            [],
            [mirror_snapshot],
            [controller_snapshot],
        )
        if rollback_errors or isinstance(exc, RemoteMirrorRollbackError):
            raise _partial_dirty(exc, rollback_errors) from exc
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _build_single_remote_response(
    service: TerrariumService,
    *,
    sid: str,
    meta: dict[str, Any],
    on_node: str,
    path: Path,
    session_name: str,
    worker_pwd_exists: bool | None,
    remote_session_path: str,
    resumed_creatures: list[dict[str, Any]],
) -> dict[str, Any]:
    """Publish a complete remote handle after adoption and mirror persistence."""
    creatures_payload = resumed_creatures or [
        {"creature_id": str(agent), "name": str(agent)}
        for agent in (meta.get("agents") or [])
        if agent
    ]
    creature_ids = [
        str(creature["creature_id"])
        for creature in creatures_payload
        if creature.get("creature_id")
    ]
    primary_cid = creature_ids[0] if creature_ids else ""
    home = getattr(service, "_home", None)
    if isinstance(home, dict):
        for creature_id in creature_ids:
            home[creature_id] = on_node
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
            "creature_ids": creature_ids,
        },
    )
    name = meta.get("terrarium_name") or session_name
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
    if worker_pwd_exists is not None:
        synthetic.pwd_exists = worker_pwd_exists
    return {
        "instance_id": sid,
        "type": ("terrarium" if meta.get("config_type") == "terrarium" else "agent"),
        "session_name": name,
        "session": asdict(synthetic),
        "on_node": on_node,
    }


def _resume_intent(body: ResumeRequest) -> str:
    """Return a stable semantic identity for coordinator singleflight."""

    def flat(values: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted((str(key), str(value)) for key, value in (values or {}).items())
        )

    nested = tuple(
        sorted(
            (str(member_id), flat(replacements))
            for member_id, replacements in (
                body.member_workspace_overrides or {}
            ).items()
        )
    )
    members = tuple(
        sorted((member.sid, member.on_node) for member in (body.members or []))
    )
    return repr(
        (
            body.on_node or "_host",
            body.pwd,
            members,
            flat(body.workspace_overrides),
            nested,
            flat(body.member_pwd_overrides),
        )
    )


def _partial_dirty(exc: BaseException, failures: list[str]) -> HTTPException:
    return HTTPException(
        status_code=502,
        detail={
            "code": "partial_dirty",
            "message": str(exc),
            "rollback_failures": failures,
            "cleanup_failures": failures,
        },
    )


def _reject_lab_host_target(request: Request, on_node: str) -> None:
    """Reject host-local adoption before resolving any saved-session path."""
    if (
        on_node != "_host"
        or getattr(request.app.state, "lab_mode", "standalone") != "lab-host"
    ):
        return
    raise HTTPException(
        status_code=400,
        detail=(
            "lab-host mode runs no agents on the host — resume on a "
            "worker node (pass on_node=<worker name>)"
        ),
    )


def _validate_cluster_member_selection(
    requested: list[ClusterMember] | None,
    saved: list[ClusterMember] | None,
    *,
    primary_sid: str,
    primary_on_node: str | None,
) -> None:
    """Validate persisted membership and the selected primary before mutation."""
    selected = requested or saved
    if requested is not None and saved is not None:
        requested_ids = [member.sid for member in requested]
        saved_ids = [member.sid for member in saved]
        if len(requested_ids) != len(saved_ids) or set(requested_ids) != set(saved_ids):
            raise HTTPException(
                status_code=400,
                detail=(
                    "cluster resume members must include every persisted "
                    "cluster member exactly once"
                ),
            )
    if not selected:
        return
    member_ids = [member.sid for member in selected]
    if len(member_ids) != len(set(member_ids)):
        raise HTTPException(
            status_code=400,
            detail="cluster resume members must use unique session ids",
        )
    if len(selected) < 2:
        return
    primary_matches = [member for member in selected if member.sid == primary_sid]
    if len(primary_matches) != 1:
        raise HTTPException(
            status_code=400,
            detail="cluster resume members must contain the requested primary session",
        )
    if primary_on_node is not None and primary_matches[0].on_node != primary_on_node:
        raise HTTPException(
            status_code=400,
            detail="cluster primary worker does not match on_node",
        )


def _read_saved_cluster_members(path: Path) -> list[ClusterMember] | None:
    """Read valid persisted cluster membership from a saved store.

    Missing, malformed, or singleton membership returns ``None``. The blocking
    store access must run through :func:`asyncio.to_thread`.
    """
    if not path.exists():
        return None
    try:
        raw = read_session_meta(path).get("cluster_members")
    except Exception:
        return None
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


def _read_saved_session_id(path: Path) -> str:
    """Return the persisted graph identity even when its file was renamed."""
    if not path.exists():
        return ""
    try:
        return str(read_session_meta(path).get("session_id") or "")
    except Exception:
        return ""


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
    workspace_overrides: dict[str, str] | None = None,
    member_workspace_overrides: dict[str, dict[str, str]] | None = None,
    member_pwd_overrides: dict[str, str] | None = None,
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
    if workspace_overrides and len(ordered) > 1:
        raise HTTPException(
            status_code=422,
            detail="Cluster workspace overrides must be scoped by member session ID",
        )
    known_member_ids = set(member_ids)
    unknown_override_ids = (
        set(member_workspace_overrides or {}) | set(member_pwd_overrides or {})
    ) - known_member_ids
    if unknown_override_ids:
        raise HTTPException(
            status_code=422,
            detail=(
                "Cluster workspace overrides reference unknown member sessions: "
                f"{sorted(unknown_override_ids)!r}"
            ),
        )
    member_overrides: dict[str, dict[str, str] | None] = {}
    member_pwds: dict[str, str | None] = {}
    for member in ordered:
        replacements = (member_workspace_overrides or {}).get(member.sid)
        if len(ordered) == 1 and replacements is None:
            replacements = workspace_overrides
        member_overrides[member.sid] = replacements
        member_pwd = (member_pwd_overrides or {}).get(member.sid)
        member_pwds[member.sid] = member_pwd if member_pwd is not None else pwd_override

    # Every worker validates its local filesystem before the first store is
    # transferred or adopted, preventing a partially resumed cluster.
    for member in ordered:
        await _worker_workspace_preflight(
            host,
            paths[member.sid],
            member.on_node,
            replacements=member_overrides[member.sid],
            pwd_override=member_pwds[member.sid],
        )

    resumed: dict[str, tuple[str, dict, str]] = {}
    remote_paths: dict[str, str] = {}
    worker_creatures_by_member: dict[str, list[dict[str, Any]]] = {}
    registered_session_ids: list[str] = []
    connected_pairs: list[tuple[str, str]] = []
    mirror_snapshots: list[Any] = []
    controller_snapshots = []
    try:
        for m in ordered:
            (
                new_sid,
                new_meta,
                _member_pwd_exists,
                remote_session_path,
                worker_creatures,
            ) = await _push_and_resume_member(
                host=host,
                request=request,
                path=paths[m.sid],
                on_node=m.on_node,
                pwd_override=member_pwds[m.sid],
                workspace_overrides=member_overrides[m.sid],
            )
            resumed[m.sid] = (new_sid, new_meta, m.on_node)
            remote_paths[m.sid] = remote_session_path
            worker_creatures_by_member[m.sid] = worker_creatures
        new_session_ids = [new_sid for new_sid, _meta, _node in resumed.values()]
        if len(set(new_session_ids)) != len(new_session_ids):
            raise RuntimeError("workers returned duplicate resumed session ids")
        durable_members = [
            {"sid": resumed[item.sid][0], "on_node": item.on_node} for item in ordered
        ]
        for member in ordered:
            _sid, new_meta, node = resumed[member.sid]
            snapshot = await asyncio.to_thread(
                _persist_remote_workspace_meta,
                paths[member.sid],
                new_meta,
                node,
                cluster_members=(
                    durable_members if member.sid == primary_member.sid else None
                ),
            )
            mirror_snapshots.append(snapshot)
    except BaseException as exc:
        rollback_errors = await rollback_cluster_resume(
            service,
            resumed,
            registered_session_ids,
            connected_pairs,
            mirror_snapshots,
            controller_snapshots,
        )
        if rollback_errors or isinstance(exc, RemoteMirrorRollbackError):
            raise _partial_dirty(exc, rollback_errors) from exc
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(
            status_code=502, detail=f"cluster member resume failed: {exc}"
        ) from exc

    # Roster refresh replaces stale pre-stop routing identities before relink.
    new_creature_ids_by_member: dict[str, list[str]] = {
        member_id: [
            str(creature["creature_id"])
            for creature in creatures
            if creature.get("creature_id")
        ]
        for member_id, creatures in worker_creatures_by_member.items()
    }
    list_creatures = getattr(service, "list_creatures", None)
    roster: tuple = ()
    if callable(list_creatures):
        try:
            roster = tuple(await list_creatures())
        except Exception:  # pragma: no cover - defensive
            roster = ()
    for original_sid, (new_sid, _meta, _node) in resumed.items():
        creature_ids: list[str] = []
        for c in roster:
            if getattr(c, "graph_id", None) == new_sid:
                creature_ids.append(str(c.creature_id))
        if creature_ids:
            new_creature_ids_by_member[original_sid] = creature_ids

    # Register every adopted member before relinking so metadata-backed
    # lookups observe a complete cluster.
    try:
        for original_sid, (new_sid, new_meta, node) in resumed.items():
            creature_ids = new_creature_ids_by_member.get(original_sid, [])
            creature_id = (
                creature_ids[0] if creature_ids else (new_meta.get("agents") or [""])[0]
            )
            published_creature_ids = creature_ids or (
                [str(creature_id)] if creature_id else []
            )
            controller_snapshots.append(
                snapshot_controller_state(
                    service,
                    new_sid,
                    published_creature_ids,
                )
            )
            home = getattr(service, "_home", None)
            if isinstance(home, dict):
                for cid in published_creature_ids:
                    home[cid] = node
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
                    "creature_ids": published_creature_ids,
                },
            )
            registered_session_ids.append(new_sid)
    except BaseException as exc:
        rollback_errors = await rollback_cluster_resume(
            service,
            resumed,
            registered_session_ids,
            connected_pairs,
            mirror_snapshots,
            controller_snapshots,
        )
        if rollback_errors:
            raise _partial_dirty(exc, rollback_errors) from exc
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(
            status_code=502, detail=f"cluster registration failed: {exc}"
        ) from exc

    # Default-channel connections rebuild cluster links from the primary.
    primary_creature_ids = new_creature_ids_by_member.get(primary_member.sid, [])
    primary_cid = primary_creature_ids[0] if primary_creature_ids else None
    try:
        if not primary_cid:
            raise RuntimeError("resumed primary creature is missing from the roster")
        for m in ordered[1:]:
            peer_creature_ids = new_creature_ids_by_member.get(m.sid, [])
            peer_cid = peer_creature_ids[0] if peer_creature_ids else None
            if not peer_cid:
                raise RuntimeError(
                    f"resumed member {m.sid!r} is missing from the roster"
                )
            connected_pairs.append((primary_cid, peer_cid))
            await service.connect(primary_cid, peer_cid, channel="default")
    except BaseException as exc:
        rollback_errors = await rollback_cluster_resume(
            service,
            resumed,
            registered_session_ids,
            connected_pairs,
            mirror_snapshots,
            controller_snapshots,
        )
        if rollback_errors:
            raise _partial_dirty(exc, rollback_errors) from exc
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(
            status_code=502, detail=f"cluster relink failed: {exc}"
        ) from exc

    primary_new_sid, primary_meta, _ = resumed[primary_member.sid]
    name = primary_meta.get("terrarium_name") or session_name
    creatures_payload: list[dict] = [
        {**creature, "home_node": primary_member.on_node}
        for creature in worker_creatures_by_member.get(primary_member.sid, [])
    ]
    refreshed_primary_creatures: list[dict] = []
    for c in roster:
        if getattr(c, "graph_id", None) != primary_new_sid:
            continue
        refreshed_primary_creatures.append(
            {
                "creature_id": c.creature_id,
                "name": c.name,
                "home_node": primary_member.on_node,
                "running": getattr(c, "is_running", True),
                "is_privileged": getattr(c, "is_privileged", False),
            }
        )
    if refreshed_primary_creatures:
        creatures_payload = refreshed_primary_creatures
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
