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
from kohakuterrarium.api.routes.persistence.resume_cluster import (
    ClusterMember,
    read_saved_cluster_members as _read_saved_cluster_members,
    read_saved_session_id as _read_saved_session_id,
    resume_cluster as _resume_cluster_impl,
    validate_cluster_member_selection as _validate_cluster_member_selection,
)
from kohakuterrarium.api.routes.persistence.resume_remote import (
    RemoteMirrorRollbackError,
    persist_remote_workspace_meta as _persist_remote_workspace_meta,
    worker_workspace_preflight as _worker_workspace_preflight,
)
from kohakuterrarium.api.routes.persistence.resume_request import (
    partial_dirty as _partial_dirty,
    reject_lab_host_target as _reject_lab_host_target,
    resume_intent as _resume_intent,
)
from kohakuterrarium.api.routes.persistence.resume_response import (
    build_single_remote_response as _build_single_remote_response,
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
from kohakuterrarium.terrarium.resume import prepare_resume_workspace
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


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

    resumed_creatures: list[dict] = [
        {**creature, "home_node": on_node} for creature in worker_creatures
    ]
    snapshot_creature_ids = [
        str(creature["creature_id"])
        for creature in resumed_creatures
        if creature.get("creature_id")
    ] or [str(agent) for agent in (meta.get("agents") or []) if agent]
    # Capture controller state before list_creatures() can refresh routing.
    controller_snapshot = snapshot_controller_state(
        service,
        sid,
        snapshot_creature_ids,
    )

    # Resumed creatures bypass spawn-time home and name cache population.
    # Refreshing the worker roster makes subsequent creature, history, and
    # chat lookups route to the correct node.
    list_creatures = getattr(service, "list_creatures", None)
    if callable(list_creatures):
        try:
            roster = await list_creatures()
        except asyncio.CancelledError as exc:
            rollback_errors = await rollback_cluster_resume(
                service,
                {"single": (sid, meta, on_node)},
                [],
                [],
                [mirror_snapshot],
                [controller_snapshot],
            )
            if rollback_errors:
                raise _partial_dirty(exc, rollback_errors) from exc
            raise
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


async def _resume_cluster(*args, **kwargs) -> dict:
    """Resume a cluster while preserving route-level monkeypatch boundaries."""
    return await _resume_cluster_impl(
        *args,
        **kwargs,
        resolve_session_path=resolve_session_path_in,
        worker_workspace_preflight=_worker_workspace_preflight,
        push_and_resume_member=_push_and_resume_member,
        persist_remote_workspace_meta=_persist_remote_workspace_meta,
        partial_dirty=_partial_dirty,
        remote_mirror_rollback_error=RemoteMirrorRollbackError,
    )
