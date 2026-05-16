"""Active sessions — engine-backed lifecycle endpoints.

Mounted at ``/api/sessions/active``.

A *session* is one engine graph regardless of how many creatures live
in it. There is no creature-vs-terrarium distinction at the API level —
both creation paths (one starts from a creature config, the other from
a recipe) produce the same shape, and a single ``GET /{id}`` route
returns it. Legacy ``/agents`` / ``/terrariums`` endpoints stay as
thin shims so older clients keep working without forking the wire
contract.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.schemas import (
    AgentCreate,
    CreatureAdd,
    RenameRequest,
    TerrariumCreate,
)
from kohakuterrarium.studio.sessions import lifecycle, remote_meta
from kohakuterrarium.terrarium.config import CreatureConfig
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class CreaturePayload(BaseModel):
    """Body for ``POST /api/sessions/active/creature``."""

    config_path: str
    llm: str | None = None
    pwd: str | None = None
    name: str | None = None
    on_node: str | None = None  # Lab target node; absent = ``_host``


# ─── creation ─────────────────────────────────────────────────────────


@router.post("/creature")
async def create_creature_session(
    req: CreaturePayload, service: TerrariumService = Depends(get_service)
):
    """Start a 1-creature session.  Returns the new session handle.

    Pass ``on_node`` to target a worker (lab-host mode); absent =
    ``_host``.  Standalone mode silently ignores it.
    """
    try:
        session = await lifecycle.start_creature(
            service,
            config_path=req.config_path,
            llm_override=req.llm,
            pwd=req.pwd,
            name=req.name,
            on_node=req.on_node or "_host",
        )
        return {**session.to_dict(), "status": "running"}
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


@router.post("/terrarium")
async def create_terrarium_session(
    req: TerrariumCreate, service: TerrariumService = Depends(get_service)
):
    """Start a multi-creature terrarium session from a recipe.

    NOTE: recipe-spawn-on-worker is not yet wired — terrarium recipes
    apply on the host engine.  If a non-host ``on_node`` is supplied
    we 501 so the frontend's SitePicker selection isn't silently
    dropped.
    """
    if req.on_node and req.on_node != "_host":
        raise HTTPException(
            501,
            "Recipe spawn on a remote worker is not implemented yet — "
            "spawn individual creatures via /agents with on_node instead.",
        )
    try:
        session = await lifecycle.start_terrarium(
            service,
            config_path=req.config_path,
            pwd=req.pwd,
            name=req.name,
            llm_override=req.llm,
        )
        return {**session.to_dict(), "status": "running"}
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


# Legacy creation aliases — preserved so older frontend callers still
# work without a forced cutover. They both produce the same Session
# shape; the only divergence is the response key (``agent_id`` /
# ``terrarium_id``) the historical caller expected.


@router.post("/agents")
async def create_agent_compat(
    req: AgentCreate, service: TerrariumService = Depends(get_service)
):
    try:
        session = await lifecycle.start_creature(
            service,
            config_path=req.config_path,
            llm_override=req.llm,
            pwd=req.pwd,
            name=req.name,
            on_node=req.on_node or "_host",
        )
        creature_id = (
            session.creatures[0].get("creature_id") if session.creatures else ""
        )
        return {
            "agent_id": creature_id,
            "session_id": session.session_id,
            "status": "running",
        }
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


@router.post("/terrariums")
async def create_terrarium_compat(
    req: TerrariumCreate, service: TerrariumService = Depends(get_service)
):
    if req.on_node and req.on_node != "_host":
        raise HTTPException(
            501,
            "Recipe spawn on a remote worker is not implemented yet — "
            "spawn individual creatures via /agents with on_node instead.",
        )
    try:
        session = await lifecycle.start_terrarium(
            service,
            config_path=req.config_path,
            pwd=req.pwd,
            name=req.name,
            llm_override=req.llm,
        )
        return {"terrarium_id": session.session_id, "status": "running"}
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


# ─── rename ──────────────────────────────────────────────────────────


@router.post("/agents/{creature_id}/rename")
async def rename_agent(
    creature_id: str,
    req: RenameRequest,
    service: TerrariumService = Depends(get_service),
):
    try:
        return await asyncio.to_thread(
            lifecycle.rename_creature, service, creature_id, req.name
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/terrariums/{session_id}/rename")
async def rename_terrarium(
    session_id: str,
    req: RenameRequest,
    service: TerrariumService = Depends(get_service),
):
    try:
        sess = await asyncio.to_thread(
            lifecycle.rename_session, service, session_id, req.name
        )
        return {"session_id": sess.session_id, "name": sess.name}
    except KeyError:
        raise HTTPException(404, f"session {session_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{session_id}/creatures/{creature_id}/rename")
async def rename_session_creature(
    session_id: str,
    creature_id: str,
    req: RenameRequest,
    service: TerrariumService = Depends(get_service),
):
    try:
        return await asyncio.to_thread(
            lifecycle.rename_creature, service, creature_id, req.name
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── unified session resolution / read ───────────────────────────────


async def _resolve_session(service: TerrariumService, identifier: str):
    """Return the live :class:`Session` for ``identifier``, accepting
    either a session_id (graph_id) or a creature_id. The runtime
    engine has no agent-vs-terrarium distinction; this resolver lets
    bookmarked URLs from before a graph grew past one member keep
    resolving to the same session without a forced redirect.

    Creature resolution routes through the service Protocol so a
    creature on a worker node resolves the same as a host-local one.

    Uses the async variant of :func:`lifecycle.get_session` so remote
    sessions refresh their cached ``model`` / ``llm_name`` from the
    worker before returning — this is what makes the model chip
    survive a chat-tab close + reopen for worker-hosted creatures
    (B3 / B4).
    """
    try:
        return await lifecycle.get_session_async(service, identifier)
    except KeyError:
        gid = await lifecycle.find_session_for_creature(service, identifier)
        if gid is not None:
            return await lifecycle.get_session_async(service, gid)
        raise


@router.delete("/agents/{creature_id}")
async def stop_creature_by_id(
    creature_id: str, service: TerrariumService = Depends(get_service)
):
    sid = await lifecycle.find_session_for_creature(service, creature_id)
    if sid is None:
        raise HTTPException(404, f"Agent not found: {creature_id}")
    try:
        await lifecycle.stop_session(service, sid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"status": "stopped"}


@router.delete("/terrariums/{session_id}")
async def stop_terrarium_session(
    session_id: str, service: TerrariumService = Depends(get_service)
):
    try:
        await lifecycle.stop_session(service, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"status": "stopped"}


@router.get("/agents")
async def list_active_agents(service: TerrariumService = Depends(get_service)):
    """Legacy alias — returns sessions whose graph holds exactly one
    creature (the original ``agent`` shape). Multi-creature sessions
    that grew via ``group_add_node`` migrate to the terrarium list.

    Refreshes remote ``_meta`` before reading so worker-side
    ``switch_model`` paths that bypass the host route do not leave
    this listing surfacing the stale identifier (S6-2)."""
    await remote_meta.refresh_all_remote_creature_meta(lifecycle._meta, service)
    return await asyncio.to_thread(_list_solo_legacy_sync, service)


@router.get("/terrariums")
async def list_active_terrariums(service: TerrariumService = Depends(get_service)):
    """Legacy alias — returns sessions whose graph holds 2+ creatures
    OR was created from a terrarium recipe.

    Refreshes remote ``_meta`` before reading so worker-side
    ``switch_model`` paths that bypass the host route do not leave
    this listing surfacing the stale identifier (S6-2)."""
    await remote_meta.refresh_all_remote_creature_meta(lifecycle._meta, service)
    return await asyncio.to_thread(_list_multi_legacy_sync, service)


@router.get("/agents/{creature_id}")
async def get_creature_status(
    creature_id: str, service: TerrariumService = Depends(get_service)
):
    """Legacy ``/agents/{id}`` accessor. Accepts either a creature_id
    or a session_id and returns the unified session shape."""
    try:
        sess = await _resolve_session(service, creature_id)
    except KeyError:
        raise HTTPException(404, f"Agent not found: {creature_id}")
    return _session_legacy_agent_response(sess)


@router.get("/terrariums/{session_id}")
async def get_terrarium_session(
    session_id: str, service: TerrariumService = Depends(get_service)
):
    """Legacy ``/terrariums/{id}`` accessor. Accepts either a
    session_id or a creature_id and returns the unified session
    shape under the historical terrarium-style keys."""
    try:
        sess = await _resolve_session(service, session_id)
    except KeyError:
        raise HTTPException(404, f"Terrarium not found: {session_id}")
    return _session_legacy_terrarium_response(sess)


@router.get("")
async def list_active_sessions(service: TerrariumService = Depends(get_service)):
    """Canonical list endpoint — every active session in the unified
    shape. Frontend stores prefer this over the legacy aliases.

    Refreshes remote ``_meta`` before reading so worker-side
    ``switch_model`` paths that bypass the host route (``/model`` slash
    command, ``PluginContext.switch_model``, compact-LLM swap) still
    surface on the next list read (S6-2)."""
    await remote_meta.refresh_all_remote_creature_meta(lifecycle._meta, service)
    sessions = await asyncio.to_thread(lifecycle.list_sessions, service)
    return [s.to_dict() for s in sessions]


@router.get("/{session_id}")
async def get_active_session(
    session_id: str, service: TerrariumService = Depends(get_service)
):
    """Canonical session getter. Accepts either a session_id or a
    creature_id; both resolve to the same unified shape."""
    try:
        sess = await _resolve_session(service, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return sess.to_dict()


@router.delete("/{session_id}")
async def stop_active_session(
    session_id: str, service: TerrariumService = Depends(get_service)
):
    try:
        await lifecycle.stop_session(service, session_id)
        return {"status": "stopped"}
    except KeyError as e:
        raise HTTPException(404, str(e))


# ─── per-session creature CRUD (hot-plug) ────────────────────────────


@router.get("/{session_id}/creatures")
async def list_session_creatures(
    session_id: str, service: TerrariumService = Depends(get_service)
):
    # S6-2: refresh the cached model from the worker before reading so
    # ``/model`` slash, plugin, and compact swap paths surface.
    try:
        await lifecycle.refresh_remote_creature_meta(service, session_id)
        return await asyncio.to_thread(lifecycle.list_creatures, service, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/{session_id}/creatures")
async def add_session_creature(
    session_id: str, req: CreatureAdd, service: TerrariumService = Depends(get_service)
):
    # ``CreatureConfig`` is ``(name, config_data: dict, base_dir: Path,
    # listen_channels, send_channels, ...)`` — NOT a ``config_path``
    # field. The request carries a path, so wrap it as a ``base_config``
    # reference in the config dict and let ``build_agent_config`` resolve
    # the inheritance (same shape ``terrarium.config._parse_creature``
    # produces for recipe creatures).
    cfg = CreatureConfig(
        name=req.name,
        config_data={"name": req.name, "base_config": req.config_path},
        base_dir=Path.cwd(),
        listen_channels=req.listen_channels,
        send_channels=req.send_channels,
    )
    try:
        cid = await lifecycle.add_creature(service, session_id, cfg)
        return {"creature_id": cid, "status": "running"}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@router.delete("/{session_id}/creatures/{creature_id}")
async def remove_session_creature(
    session_id: str, creature_id: str, service: TerrariumService = Depends(get_service)
):
    try:
        removed = await lifecycle.remove_creature(service, session_id, creature_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if not removed:
        raise HTTPException(404, f"creature {creature_id!r} not found in session")
    return {"status": "removed"}


# ─── legacy shape adapters ───────────────────────────────────────────


def _session_legacy_agent_response(sess) -> dict:
    """Shape a Session into the legacy agent response — preserves the
    fields ``stores/instances._mapAgent`` reads. The full graph roster
    is surfaced under ``graph_*`` so the frontend can transparently
    show multi-creature panels for a graph that grew past one member."""
    primary = sess.creatures[0] if sess.creatures else {}
    out = dict(primary)
    out["agent_id"] = primary.get("creature_id") or primary.get("agent_id") or ""
    out["graph_id"] = sess.session_id
    out["graph_creatures"] = list(sess.creatures)
    out["graph_channels"] = list(sess.channels)
    out["graph_creature_count"] = len(sess.creatures) or 1
    if sess.has_root:
        out["has_root"] = True
    return out


def _session_legacy_terrarium_response(sess) -> dict:
    """Shape a Session into the legacy terrarium response."""
    creatures = {c.get("name", c.get("creature_id", "")): c for c in sess.creatures}
    root_status: dict = {}
    if sess.has_root:
        root_status = creatures.get("root") or next(
            (c for c in sess.creatures if c.get("is_root")),
            {},
        )
    out = {
        "terrarium_id": sess.session_id,
        "name": sess.name,
        "running": True,
        "creatures": creatures,
        "channels": sess.channels,
        "has_root": sess.has_root,
        "pwd": sess.pwd or root_status.get("pwd", ""),
    }
    if root_status:
        out["root_model"] = root_status.get("model", "")
        out["root_llm_name"] = root_status.get("llm_name", "")
        out["root_session_id"] = root_status.get("session_id", "")
        out["root_max_context"] = root_status.get("max_context", 0)
        out["root_compact_threshold"] = root_status.get("compact_threshold", 0)
    return out


def _list_solo_legacy_sync(service: TerrariumService) -> list[dict]:
    """Sessions with exactly one creature, in legacy agent shape."""
    out: list[dict] = []
    for listing in lifecycle.list_sessions(service):
        if listing.creatures != 1:
            continue
        full = lifecycle.get_session(service, listing.session_id)
        if full.creatures:
            out.append(_session_legacy_agent_response(full))
    return out


def _list_multi_legacy_sync(service: TerrariumService) -> list[dict]:
    """Sessions with 2+ creatures (or recipe-loaded), in legacy
    terrarium shape."""
    out: list[dict] = []
    for listing in lifecycle.list_sessions(service):
        if listing.creatures < 2:
            continue
        full = lifecycle.get_session(service, listing.session_id)
        out.append(_session_legacy_terrarium_response(full))
    return out
