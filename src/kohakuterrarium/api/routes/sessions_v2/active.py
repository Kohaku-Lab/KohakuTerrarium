"""Expose lifecycle operations for engine-backed sessions.

A session represents one engine graph regardless of creature count or whether it
originated from a creature config or a recipe. Legacy agent and terrarium routes
adapt the same session model for older clients.
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service, resolve_request_session_dir
from kohakuterrarium.api.routes.persistence.resume_coordinator import (
    conversation_coordination_key,
    resume_coordinator,
)
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
    """Describe a single-creature session to start."""

    config_path: str
    llm: str | None = None
    pwd: str | None = None
    name: str | None = None
    on_node: str | None = None  # Omission targets the lab host.


def _runtime_conversation_id(service: TerrariumService, session_id: str) -> str:
    """Return the stable conversation identity, falling back to the runtime ID."""
    store = lifecycle.get_session_store(service, session_id)
    if store is not None and not getattr(store, "_closed", False):
        try:
            conversation_id = str(store.meta.get("conversation_id") or "")
        except Exception:  # noqa: BLE001 - metadata fallback remains available
            conversation_id = ""
        if conversation_id:
            return conversation_id
    meta = lifecycle.meta_for(service).get(session_id) or {}
    return str(meta.get("conversation_id") or session_id)


async def _run_lifecycle_action(
    service: TerrariumService,
    session_id: str,
    session_dir: Path,
    *,
    verb: str,
) -> None:
    """Serialize active stop/end with saved-session resume and rail end."""
    conversation_id = _runtime_conversation_id(service, session_id)
    key = conversation_coordination_key(conversation_id, session_dir)
    action = lifecycle.end_session if verb == "end" else lifecycle.stop_session
    try:
        await resume_coordinator.run(
            key,
            lambda: action(service, session_id),
            intent=f"{verb}:{conversation_id}",
        )
    except RuntimeError as exc:
        if "conflicting resume request" in str(exc):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise


@router.post("/creature")
async def create_creature_session(
    req: CreaturePayload, service: TerrariumService = Depends(get_service)
):
    """Start a single-creature session on the host or a selected worker.

    Standalone services ignore the worker target.
    """
    try:
        session = await lifecycle.start_creature(
            service,
            config_path=req.config_path,
            llm=req.llm,
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
    """Start a multi-creature session from a host-local recipe.

    Remote recipe spawning is rejected rather than silently ignoring the selected
    worker.
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
            llm=req.llm,
        )
        return {**session.to_dict(), "status": "running"}
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


# Compatibility routes preserve historical response identifiers while using the
# unified session model.


@router.post("/agents")
async def create_agent_compat(
    req: AgentCreate, service: TerrariumService = Depends(get_service)
):
    try:
        session = await lifecycle.start_creature(
            service,
            config_path=req.config_path,
            llm=req.llm,
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
            llm=req.llm,
        )
        return {"terrarium_id": session.session_id, "status": "running"}
    except (ValueError, KeyError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))


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


async def _resolve_session(service: TerrariumService, identifier: str):
    """Resolve a live session from either a graph or creature identifier.

    Creature lookup is service-routed for worker-hosted sessions. Async session
    reads refresh cached remote model metadata before returning, preserving
    bookmarked creature URLs as graphs grow.
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
    creature_id: str,
    service: TerrariumService = Depends(get_service),
    session_dir: Path = Depends(resolve_request_session_dir),
):
    sid = await lifecycle.find_session_for_creature(service, creature_id)
    if sid is None:
        raise HTTPException(404, f"Agent not found: {creature_id}")
    try:
        await _run_lifecycle_action(
            service,
            sid,
            session_dir,
            verb="stop",
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"status": "stopped"}


@router.delete("/terrariums/{session_id}")
async def stop_terrarium_session(
    session_id: str,
    service: TerrariumService = Depends(get_service),
    session_dir: Path = Depends(resolve_request_session_dir),
):
    try:
        await _run_lifecycle_action(
            service,
            session_id,
            session_dir,
            verb="stop",
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"status": "stopped"}


@router.get("/agents")
async def list_active_agents(service: TerrariumService = Depends(get_service)):
    """Return single-creature sessions in the legacy agent shape.

    Refresh remote metadata first so worker-side model switches appear in the
    listing. Sessions leave this view after gaining additional creatures.
    """
    await remote_meta.refresh_all_remote_creature_meta(
        lifecycle.meta_for(service), service
    )
    return await asyncio.to_thread(_list_solo_legacy_sync, service)


@router.get("/terrariums")
async def list_active_terrariums(service: TerrariumService = Depends(get_service)):
    """Return multi-creature sessions in the legacy terrarium shape.

    Refresh remote metadata first so worker-side model switches appear in the
    listing.
    """
    await remote_meta.refresh_all_remote_creature_meta(
        lifecycle.meta_for(service), service
    )
    return await asyncio.to_thread(_list_multi_legacy_sync, service)


@router.get("/agents/{creature_id}")
async def get_creature_status(
    creature_id: str, service: TerrariumService = Depends(get_service)
):
    """Return a graph or creature identifier in the legacy agent shape."""
    try:
        sess = await _resolve_session(service, creature_id)
    except KeyError:
        raise HTTPException(404, f"Agent not found: {creature_id}")
    return _session_legacy_agent_response(sess)


@router.get("/terrariums/{session_id}")
async def get_terrarium_session(
    session_id: str, service: TerrariumService = Depends(get_service)
):
    """Return a graph or creature identifier in the legacy terrarium shape."""
    try:
        sess = await _resolve_session(service, session_id)
    except KeyError:
        raise HTTPException(404, f"Terrarium not found: {session_id}")
    return _session_legacy_terrarium_response(sess)


@router.get("")
async def list_active_sessions(service: TerrariumService = Depends(get_service)):
    """Return every active session in the unified shape.

    Refresh remote metadata first so model changes made directly on workers are
    visible on the next read.
    """
    await remote_meta.refresh_all_remote_creature_meta(
        lifecycle.meta_for(service), service
    )
    sessions = await asyncio.to_thread(lifecycle.list_sessions, service)
    return [s.to_dict() for s in sessions]


@router.get("/{session_id}")
async def get_active_session(
    session_id: str, service: TerrariumService = Depends(get_service)
):
    """Return one unified session by graph or creature identifier."""
    try:
        sess = await _resolve_session(service, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return sess.to_dict()


@router.post("/{session_id}/end")
async def end_active_session(
    session_id: str,
    service: TerrariumService = Depends(get_service),
    session_dir: Path = Depends(resolve_request_session_dir),
):
    """Explicitly end a conversation and remove its runtime."""
    try:
        await _run_lifecycle_action(
            service,
            session_id,
            session_dir,
            verb="end",
        )
        return {"status": "ended"}
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.delete("/{session_id}")
async def stop_active_session(
    session_id: str,
    service: TerrariumService = Depends(get_service),
    session_dir: Path = Depends(resolve_request_session_dir),
):
    try:
        await _run_lifecycle_action(
            service,
            session_id,
            session_dir,
            verb="stop",
        )
        return {"status": "stopped"}
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.get("/{session_id}/creatures")
async def list_session_creatures(
    session_id: str, service: TerrariumService = Depends(get_service)
):
    # Refresh worker metadata so out-of-band model switches are visible.
    try:
        await lifecycle.refresh_remote_creature_meta(service, session_id)
        return await asyncio.to_thread(lifecycle.list_creatures, service, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/{session_id}/creatures")
async def add_session_creature(
    session_id: str, req: CreatureAdd, service: TerrariumService = Depends(get_service)
):
    # CreatureConfig accepts inherited config data rather than a path field, so
    # preserve recipe parsing semantics by passing the request path as base_config.
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


def _session_legacy_agent_response(sess) -> dict:
    """Convert a session to the legacy agent shape with full graph metadata."""
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
    """Return multi-creature sessions in the legacy terrarium shape."""
    out: list[dict] = []
    for listing in lifecycle.list_sessions(service):
        if listing.creatures < 2:
            continue
        full = lifecycle.get_session(service, listing.session_id)
        out.append(_session_legacy_terrarium_response(full))
    return out
