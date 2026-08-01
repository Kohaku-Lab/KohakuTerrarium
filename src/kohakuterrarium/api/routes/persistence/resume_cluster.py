"""Cluster-member validation, persistence lookup, and remote adoption."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

from kohakuterrarium.api.routes.persistence.cluster_resume_compensation import (
    rollback_cluster_resume,
    snapshot_controller_state,
)
from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.studio.sessions.handles import Session
from kohakuterrarium.studio.sessions.lifecycle import now_iso, register_session_meta
from kohakuterrarium.terrarium.service import TerrariumService


class ClusterMember(BaseModel):
    """One member of a cluster session."""

    sid: str
    on_node: str


def validate_cluster_member_selection(
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


def read_saved_cluster_members(path: Path) -> list[ClusterMember] | None:
    """Read valid persisted cluster membership from a saved store.

    An absent field returns ``None`` for a single-session resume. Present but
    unreadable or malformed membership fails closed before any runtime mutation.
    The blocking store access must run through :func:`asyncio.to_thread`.
    """
    if not path.exists():
        return None
    try:
        meta = read_session_meta(path)
    except Exception as exc:
        raise _corrupt_cluster_members(
            f"Unable to read persisted cluster membership: {exc}"
        ) from exc
    if not isinstance(meta, dict):
        raise _corrupt_cluster_members("Persisted session metadata is not an object")
    if "cluster_members" not in meta:
        return None
    raw = meta["cluster_members"]
    if not isinstance(raw, list) or len(raw) < 2:
        raise _corrupt_cluster_members(
            "Persisted cluster_members must contain at least two members"
        )
    members: list[ClusterMember] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise _corrupt_cluster_members(
                f"Persisted cluster member at index {index} is not an object"
            )
        sid = entry.get("sid")
        node = entry.get("on_node")
        if not (isinstance(sid, str) and sid and isinstance(node, str) and node):
            raise _corrupt_cluster_members(
                f"Persisted cluster member at index {index} requires sid and on_node"
            )
        members.append(ClusterMember(sid=sid, on_node=node))
    return members


def _corrupt_cluster_members(message: str) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "corrupt_cluster_members", "message": message},
    )


def read_saved_session_id(path: Path) -> str:
    """Return the persisted graph identity even when its file was renamed."""
    if not path.exists():
        return ""
    try:
        return str(read_session_meta(path).get("session_id") or "")
    except Exception:
        return ""


async def resume_cluster(
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
    resolve_session_path: Callable[[str, Path], Path | None],
    worker_workspace_preflight: Callable[..., Awaitable[dict[str, Any]]],
    push_and_resume_member: Callable[..., Awaitable[tuple]],
    persist_remote_workspace_meta: Callable[..., Any],
    partial_dirty: Callable[[BaseException, list], HTTPException],
    remote_mirror_rollback_error: type[BaseException],
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

    paths: dict[str, Path] = {}
    for member in members:
        resolved = await asyncio.to_thread(
            resolve_session_path, member.sid, session_dir
        )
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "CF-6 cluster resume: no saved store for member "
                    f"sid={member.sid!r}"
                ),
            )
        paths[member.sid] = resolved

    ordered: list[ClusterMember] = [primary_member] + [
        member for member in members if member.sid != primary_member.sid
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

    for member in ordered:
        await worker_workspace_preflight(
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
        for member in ordered:
            (
                new_sid,
                new_meta,
                _member_pwd_exists,
                remote_session_path,
                worker_creatures,
            ) = await push_and_resume_member(
                host=host,
                request=request,
                path=paths[member.sid],
                on_node=member.on_node,
                pwd_override=member_pwds[member.sid],
                workspace_overrides=member_overrides[member.sid],
            )
            resumed[member.sid] = (new_sid, new_meta, member.on_node)
            remote_paths[member.sid] = remote_session_path
            worker_creatures_by_member[member.sid] = worker_creatures
        new_session_ids = [new_sid for new_sid, _meta, _node in resumed.values()]
        if len(set(new_session_ids)) != len(new_session_ids):
            raise RuntimeError("workers returned duplicate resumed session ids")
        durable_members = [
            {"sid": resumed[item.sid][0], "on_node": item.on_node} for item in ordered
        ]
        for member in ordered:
            _sid, new_meta, node = resumed[member.sid]
            snapshot = await asyncio.to_thread(
                persist_remote_workspace_meta,
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
        if rollback_errors or isinstance(exc, remote_mirror_rollback_error):
            raise partial_dirty(exc, rollback_errors) from exc
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(
            status_code=502, detail=f"cluster member resume failed: {exc}"
        ) from exc

    new_creature_ids_by_member: dict[str, list[str]] = {
        member_id: [
            str(creature["creature_id"])
            for creature in creatures
            if creature.get("creature_id")
        ]
        for member_id, creatures in worker_creatures_by_member.items()
    }
    for original_sid, (_new_sid, member_meta, _node) in resumed.items():
        if not new_creature_ids_by_member.get(original_sid):
            new_creature_ids_by_member[original_sid] = [
                str(agent) for agent in (member_meta.get("agents") or []) if agent
            ]
    controller_snapshots.extend(
        snapshot_controller_state(
            service,
            resumed[member.sid][0],
            new_creature_ids_by_member.get(member.sid, []),
        )
        for member in ordered
    )
    list_creatures = getattr(service, "list_creatures", None)
    roster: tuple = ()
    if callable(list_creatures):
        try:
            roster = tuple(await list_creatures())
        except asyncio.CancelledError as exc:
            rollback_errors = await rollback_cluster_resume(
                service,
                resumed,
                registered_session_ids,
                connected_pairs,
                mirror_snapshots,
                controller_snapshots,
            )
            if rollback_errors:
                raise partial_dirty(exc, rollback_errors) from exc
            raise
        except Exception:  # pragma: no cover - defensive
            roster = ()
    for original_sid, (new_sid, _meta, _node) in resumed.items():
        creature_ids: list[str] = []
        for creature in roster:
            if getattr(creature, "graph_id", None) == new_sid:
                creature_ids.append(str(creature.creature_id))
        if creature_ids:
            new_creature_ids_by_member[original_sid] = creature_ids

    try:
        for original_sid, (new_sid, new_meta, node) in resumed.items():
            creature_ids = new_creature_ids_by_member.get(original_sid, [])
            creature_id = (
                creature_ids[0] if creature_ids else (new_meta.get("agents") or [""])[0]
            )
            published_creature_ids = creature_ids or (
                [str(creature_id)] if creature_id else []
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
            raise partial_dirty(exc, rollback_errors) from exc
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise HTTPException(
            status_code=502, detail=f"cluster registration failed: {exc}"
        ) from exc

    primary_creature_ids = new_creature_ids_by_member.get(primary_member.sid, [])
    primary_cid = primary_creature_ids[0] if primary_creature_ids else None
    try:
        if not primary_cid:
            raise RuntimeError("resumed primary creature is missing from the roster")
        for member in ordered[1:]:
            peer_creature_ids = new_creature_ids_by_member.get(member.sid, [])
            peer_cid = peer_creature_ids[0] if peer_creature_ids else None
            if not peer_cid:
                raise RuntimeError(
                    f"resumed member {member.sid!r} is missing from the roster"
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
            raise partial_dirty(exc, rollback_errors) from exc
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
    for creature in roster:
        if getattr(creature, "graph_id", None) != primary_new_sid:
            continue
        refreshed_primary_creatures.append(
            {
                "creature_id": creature.creature_id,
                "name": creature.name,
                "home_node": primary_member.on_node,
                "running": getattr(creature, "is_running", True),
                "is_privileged": getattr(creature, "is_privileged", False),
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
        "terrarium" if primary_meta.get("config_type") == "terrarium" else "agent"
    )
    return {
        "instance_id": primary_new_sid,
        "type": instance_type,
        "session_name": name,
        "session": asdict(synthetic),
        "on_node": primary_on_node,
        "cluster_members": [
            {"sid": new_sid, "on_node": node}
            for new_sid, _meta, node in resumed.values()
        ],
    }
