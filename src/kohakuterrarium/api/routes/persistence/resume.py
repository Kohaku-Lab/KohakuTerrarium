"""Persistence resume — adopt a saved session into the live engine.

Path is ``/{session_name}/resume`` so the router can be mounted under
``/api/sessions`` (legacy URL preservation: the frontend's
``sessionAPI.resumeSession`` calls ``POST /sessions/{name}/resume``).

Returns the legacy resume response shape ``{instance_id, type,
session_name}`` expected by ``sessionAPI.resume`` (api.js:399) plus
the full :class:`Session` handle under ``session`` for callers that
want it.

Lab-host mode:

- ``on_node = "_host"`` (or absent) — current behaviour: resume into
  the host's engine.
- ``on_node = "<worker-id>"`` — push the ``.kohakutr`` bytes to the
  worker via ``terrarium.files.write``, then call
  ``terrarium.session.resume`` so the worker's engine adopts the
  session locally.  Returns the same response shape so the frontend
  can render the resumed creature uniformly.
"""

import asyncio
from dataclasses import asdict
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_engine, get_service
from kohakuterrarium.laboratory.adapters.file_scopes import kt_config_home
from kohakuterrarium.laboratory.file_transfer import stream_write_file
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.resume import resume_session as studio_resume
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem
from kohakuterrarium.studio.sessions.handles import Session
from kohakuterrarium.studio.sessions.lifecycle import _meta as _lifecycle_meta
from kohakuterrarium.studio.sessions.lifecycle import _now_iso
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class ClusterMember(BaseModel):
    """One member of a cluster session for ``ResumeRequest.members``."""

    sid: str
    on_node: str


class ResumeRequest(BaseModel):
    """Optional body for ``POST .../{name}/resume``.

    The legacy frontend posts no body; the new field is optional and
    defaults to ``"_host"`` so existing callers are unaffected.

    ``members`` is the CF-6 cluster-resume extension: when the saved
    session was part of a cross-node cluster, the caller MUST provide
    one ``(sid, on_node)`` entry per member so every worker re-adopts
    its own ``.kohakutr`` and the host can relink them via
    :meth:`service.connect`. The route can also auto-fill this list
    from the primary's saved ``cluster_members`` meta when present.
    """

    on_node: str = "_host"
    members: list[ClusterMember] | None = None


@router.post("/{session_name}/resume")
async def resume_session(
    session_name: str,
    request: Request,
    req: ResumeRequest | None = None,
    service: TerrariumService = Depends(get_service),
):
    """Resume a saved session into the engine.

    ``get_engine`` is resolved *lazily* — only the standalone
    ``on_node="_host"`` branch needs a host engine, and lab-host mode
    rejects that branch outright.  Eagerly ``Depends(get_engine)`` here
    would resolve it on every request, emitting the spurious lab-host
    "route needs Depends(get_service)" warning even though this route
    never touches a host engine in lab-host mode.
    """
    on_node = (req.on_node if req is not None else "_host") or "_host"

    # Local-is-local, multi-node-is-multi-node — never mixed. In
    # lab-host mode (the service exposes ``connected_nodes`` — a
    # ``MultiNodeTerrariumService``) the host process runs NO agents.
    # A host-targeted resume would adopt the session into the host's
    # own engine — exactly the dual-path mixing that wedges the
    # cluster. Reject it up front; the caller must resume on a worker.
    if on_node == "_host" and hasattr(service, "connected_nodes"):
        raise HTTPException(
            status_code=400,
            detail=(
                "lab-host mode runs no agents on the host — resume on a "
                "worker node (pass on_node=<worker name>)"
            ),
        )

    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(
            status_code=404, detail=f"Session not found: {session_name}"
        )

    if on_node == "_host":
        # Standalone-only branch — lab-host rejected ``_host`` above.
        engine = get_engine()
        try:
            session = await studio_resume(engine, path)
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

    # Remote-node resume: push the .kohakutr bytes to the worker, then
    # ask its terrarium.session.resume adapter to adopt locally.  The
    # service surface is ``MultiNodeTerrariumService`` in lab-host
    # mode; standalone has no remotes and would have failed the
    # ``on_node`` validation below.
    host = getattr(service, "host", None)
    connected = (
        list(service.connected_nodes()) if hasattr(service, "connected_nodes") else []
    )
    if host is None or on_node not in connected:
        raise HTTPException(
            status_code=404,
            detail=f"on_node={on_node!r} is not a connected lab node",
        )

    # CF-6: cluster resume. The primary's saved meta may carry a
    # ``cluster_members`` list (persisted at ``stop_session`` time).
    # When present — and the caller didn't already specify the full
    # member list — we resume EVERY member on its own worker and then
    # relink them via ``service.connect`` so ``_cluster_links`` is
    # repopulated. Without this, the resume silently downgrades a
    # multi-worker cluster session to a singleton.
    requested_members = req.members if (req is not None and req.members) else None
    saved_members = await asyncio.to_thread(_read_saved_cluster_members, path)
    cluster_members = requested_members or saved_members
    if cluster_members and len(cluster_members) > 1:
        # Validate every targeted worker is currently connected before
        # we start pushing files — otherwise we'd push to some, fail on
        # others, and leave the cluster half-resumed.
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
        )

    sid, meta = await _push_and_resume_member(
        host=host,
        request=request,
        path=path,
        on_node=on_node,
    )

    # The worker adopted the session and now hosts its creature(s) — but
    # the multi-node service's ``_home`` / ``_creature_name_cache`` were
    # populated only at spawn time and know nothing about a resumed
    # creature.  Without a refresh, follow-up lookups by creature_id
    # (``get_creature_info``, history, chat) all 404 because the home
    # registry returns no node for the resumed id.  Fan out
    # ``list_creatures`` to repopulate both caches authoritatively from
    # every worker's current roster.
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

    # Register the resumed remote session in the controller's _meta so
    # ``list_sessions`` surfaces it alongside host-local sessions, and
    # so ``get_session`` / ``list_creatures`` (the studio-tier helpers)
    # can resynthesise the Session handle with a real worker creature
    # id.  Prefer the live roster's first creature_id; fall back to the
    # config-name path only when the roster fan-out came back empty.
    primary_cid = (
        resumed_creatures[0]["creature_id"]
        if resumed_creatures
        else (meta.get("agents") or [""])[0]
    )
    _lifecycle_meta[sid] = {
        "name": meta.get("terrarium_name") or meta.get("session_id") or sid,
        "config_path": meta.get("config_path", ""),
        "pwd": meta.get("pwd", ""),
        "created_at": _now_iso(),
        "on_node": on_node,
        "resumed_from": str(path),
        "creature_id": primary_cid,
    }

    name = meta.get("terrarium_name") or session_name
    # Worker-side resume already attached the creature(s); surface a
    # Session handle whose ``creatures`` carry real worker creature
    # ids so the frontend's chat / history endpoints can address them.
    # When the roster fan-out came back empty (unlikely — defensive),
    # fall back to the meta-derived placeholder shape so the response
    # is still well-formed.
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
        created_at=_now_iso(),
        config_path=meta.get("config_path", ""),
        home_node=on_node,
    )
    instance_type = "terrarium" if (meta.get("config_type") == "terrarium") else "agent"
    return {
        "instance_id": sid,
        "type": instance_type,
        "session_name": name,
        "session": asdict(synthetic),
        "on_node": on_node,
    }


def _worker_absolute_for(rel: str) -> str:
    """Reconstruct the worker-side absolute path under ``config://``.

    The worker resolves ``config://`` via
    :func:`kohakuterrarium.laboratory.adapters.file_scopes.kt_config_home`
    (the ``KT_CONFIG_DIR`` override, else ``~/.kohakuterrarium``).  We
    mirror that resolution here so the resume RPC gets an absolute path
    without an extra round-trip.  In a real multi-node deployment host
    and worker are different machines — this is correct only when both
    resolve ``config://`` the same way (same ``KT_CONFIG_DIR`` policy).
    """
    return str(kt_config_home() / rel)


def _read_saved_cluster_members(path: Path) -> list[ClusterMember] | None:
    """Read the persisted ``cluster_members`` entry from a saved store.

    Returns ``None`` when the meta has no ``cluster_members`` entry
    (the session was not part of a cluster, or it predates the CF-6
    persistence path). Otherwise returns one :class:`ClusterMember`
    per recorded sibling.

    Blocking — call via :func:`asyncio.to_thread`.
    """
    # SessionStore creates the SQLite file as a side effect of
    # ``__init__`` — guard so a ghost path stays ghost (the downstream
    # ``aiofiles.open`` is what produces the canonical 404).
    if not path.exists():
        return None
    try:
        store = SessionStore(path)
    except Exception:
        return None
    try:
        raw = store.meta.get("cluster_members")
    finally:
        store.close()
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


async def _push_and_resume_member(
    *,
    host,
    request: Request,
    path: Path,
    on_node: str,
) -> tuple[str, dict]:
    """Push one ``.kohakutr`` to ``on_node`` and call its resume RPC.

    Returns the worker-reported ``(session_id, meta)`` tuple. Raises
    :class:`HTTPException` on any push / resume failure (same shape
    the single-member path used inline before CF-6).
    """
    # The session file may be a *live* mirror store the
    # ``SessionMirrorWriter`` still holds open — its meta + recent
    # events sit in a write cache / the SQLite ``-wal`` sidecar and a
    # raw byte read would miss them (the worker then rejects the push
    # as "Session is a None"). Checkpoint the open mirror store first.
    mirror = getattr(request.app.state, "session_mirror", None)
    if mirror is not None and hasattr(mirror, "checkpoint"):
        try:
            mirror.checkpoint(normalize_session_stem(path))
        except Exception:  # pragma: no cover - defensive
            pass

    try:
        async with aiofiles.open(path, "rb") as f:
            data = await f.read()
    except OSError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    rel = f"resume/{path.name}"
    try:
        await stream_write_file(host, on_node, "config://", rel, data)
        target_path_resp = await host.request(
            to_node=on_node,
            namespace="terrarium.files",
            type="stat",
            body={"scope": "config://", "path": rel},
            timeout=10.0,
        )
        if isinstance(target_path_resp, dict) and "error" in target_path_resp:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"worker {on_node!r} failed to receive .kohakutr: "
                    f"{target_path_resp['error'].get('message', '')}"
                ),
            )
        worker_path_resp = await host.request(
            to_node=on_node,
            namespace="terrarium.session",
            type="resume",
            body={"path": _worker_absolute_for(rel)},
            timeout=60.0,
        )
        if isinstance(worker_path_resp, dict) and "error" in worker_path_resp:
            err = worker_path_resp["error"]
            # The worker classifies errors via its dispatch handler:
            # ValueError → "invalid", KeyError → "not_found", anything
            # else → "session"/"engine".  "invalid" is the user-input
            # failure case (e.g. the saved session has no rebuildable
            # config — split-graph mirrors that pre-date config_snapshot
            # inheritance), so surface it as a client error so the UI
            # can show an actionable message instead of a transport-
            # error 502.
            kind = err.get("kind") if isinstance(err, dict) else None
            status = 400 if kind in ("invalid", "not_found") else 502
            raise HTTPException(
                status_code=status,
                detail=(
                    f"worker {on_node!r} resume failed: "
                    f"{err.get('message', '') if isinstance(err, dict) else ''}"
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"lab transport error: {exc}"
        ) from exc

    sid = worker_path_resp.get("session_id", "")
    meta = worker_path_resp.get("meta", {}) or {}
    if not isinstance(sid, str) or not sid:
        raise HTTPException(
            status_code=502,
            detail=f"worker {on_node!r} returned no session_id",
        )
    return sid, meta


async def _resume_cluster(
    service: TerrariumService,
    request: Request,
    host,
    members: list[ClusterMember],
    primary_on_node: str,
    session_name: str,
    *,
    primary_sid: str,
) -> dict:
    """CF-6 — resume every cluster member then relink them.

    Steps:

    1. Resume each member's ``.kohakutr`` on its respective worker.
    2. Refresh ``service.list_creatures`` so the home / name caches
       authoritatively reflect the resumed roster.
    3. For every non-primary member, call ``service.connect()`` with
       the primary's first creature_id and the member's first
       creature_id so :attr:`MultiNodeTerrariumService._cluster_links`
       is repopulated.
    4. Return the primary's session payload (the rest are reachable
       via the service's normal listing endpoints).
    """
    # Resolve every member's saved ``.kohakutr`` upfront so a missing
    # mirror surfaces as 404 before we mutate any worker state.
    paths: dict[str, Path] = {}
    for m in members:
        resolved = await asyncio.to_thread(resolve_session_path_default, m.sid)
        if resolved is None:
            raise HTTPException(
                status_code=404,
                detail=f"CF-6 cluster resume: no saved store for member sid={m.sid!r}",
            )
        paths[m.sid] = resolved

    # Resume every member.  Order primary first so its meta is the
    # canonical response shape.
    primary_member = next(
        (m for m in members if m.sid == primary_sid),
        members[0],
    )
    ordered: list[ClusterMember] = [primary_member] + [
        m for m in members if m.sid != primary_member.sid
    ]
    resumed: dict[str, tuple[str, dict, str]] = (
        {}
    )  # original_sid -> (new_sid, meta, on_node)
    for m in ordered:
        new_sid, new_meta = await _push_and_resume_member(
            host=host,
            request=request,
            path=paths[m.sid],
            on_node=m.on_node,
        )
        resumed[m.sid] = (new_sid, new_meta, m.on_node)

    # Refresh the service roster so the home registry / name cache
    # carry the resumed creature ids.  Subsequent ``connect`` calls
    # route by home — without this the lookup falls back to caches
    # that still point at the pre-stop creatures.
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

    # Register each resumed remote session in studio meta so the
    # listing / get_session endpoints surface it.  Done before the
    # cross-node connect so the meta-driven helpers see consistent
    # state during the relink.
    for original_sid, (new_sid, new_meta, node) in resumed.items():
        creature_id = new_creature_by_member.get(original_sid) or (
            (new_meta.get("agents") or [""])[0]
        )
        _lifecycle_meta[new_sid] = {
            "name": new_meta.get("terrarium_name")
            or new_meta.get("session_id")
            or new_sid,
            "config_path": new_meta.get("config_path", ""),
            "pwd": new_meta.get("pwd", ""),
            "created_at": _now_iso(),
            "on_node": node,
            "resumed_from": str(paths[original_sid]),
            "creature_id": creature_id,
        }

    # Relink: call ``service.connect`` between the primary creature and
    # every other member's creature so cross_node_connect repopulates
    # ``_cluster_links``.  Channel name is left default — the wire
    # uses the cluster_members meta + auto-name. Failures are logged
    # but do not abort the response: the per-member resume already
    # succeeded; a relink failure just degrades to two singletons,
    # which is recoverable by manual /connect.
    primary_cid = new_creature_by_member.get(primary_member.sid)
    relink_errors: list[str] = []
    if primary_cid and hasattr(service, "connect"):
        for m in ordered[1:]:
            peer_cid = new_creature_by_member.get(m.sid)
            if not peer_cid:
                relink_errors.append(f"no creature_id for sid={m.sid}")
                continue
            try:
                await service.connect(primary_cid, peer_cid)
            except Exception as exc:  # pragma: no cover - defensive
                relink_errors.append(f"{m.sid}: {exc}")

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
        created_at=_now_iso(),
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
        "relink_errors": relink_errors,
    }
