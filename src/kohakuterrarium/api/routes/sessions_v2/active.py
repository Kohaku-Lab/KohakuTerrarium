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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.schemas import (
    AgentCreate,
    CreatureAdd,
    RenameRequest,
    TerrariumCreate,
)
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.terrarium.config import CreatureConfig

router = APIRouter()


class CreaturePayload(BaseModel):
    """Body for ``POST /api/sessions/active/creature``."""

    config_path: str
    llm: str | None = None
    pwd: str | None = None
    name: str | None = None


# ─── creation ─────────────────────────────────────────────────────────


@router.post("/creature")
async def create_creature_session(req: CreaturePayload, engine=Depends(get_engine)):
    """Start a 1-creature session.  Returns the new session handle."""
    try:
        session = await lifecycle.start_creature(
            engine,
            config_path=req.config_path,
            llm_override=req.llm,
            pwd=req.pwd,
            name=req.name,
        )
        return {**session.to_dict(), "status": "running"}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@router.post("/terrarium")
async def create_terrarium_session(req: TerrariumCreate, engine=Depends(get_engine)):
    """Start a multi-creature terrarium session from a recipe."""
    try:
        session = await lifecycle.start_terrarium(
            engine, config_path=req.config_path, pwd=req.pwd, name=req.name
        )
        return {**session.to_dict(), "status": "running"}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


# Legacy creation aliases — preserved so older frontend callers still
# work without a forced cutover. They both produce the same Session
# shape; the only divergence is the response key (``agent_id`` /
# ``terrarium_id``) the historical caller expected.


@router.post("/agents")
async def create_agent_compat(req: AgentCreate, engine=Depends(get_engine)):
    try:
        session = await lifecycle.start_creature(
            engine,
            config_path=req.config_path,
            llm_override=req.llm,
            pwd=req.pwd,
            name=req.name,
        )
        creature_id = (
            session.creatures[0].get("creature_id") if session.creatures else ""
        )
        return {
            "agent_id": creature_id,
            "session_id": session.session_id,
            "status": "running",
        }
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@router.post("/terrariums")
async def create_terrarium_compat(req: TerrariumCreate, engine=Depends(get_engine)):
    try:
        session = await lifecycle.start_terrarium(
            engine, config_path=req.config_path, pwd=req.pwd, name=req.name
        )
        return {"terrarium_id": session.session_id, "status": "running"}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


# ─── rename ──────────────────────────────────────────────────────────


@router.post("/agents/{creature_id}/rename")
async def rename_agent(
    creature_id: str, req: RenameRequest, engine=Depends(get_engine)
):
    try:
        return await asyncio.to_thread(
            lifecycle.rename_creature, engine, creature_id, req.name
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/terrariums/{session_id}/rename")
async def rename_terrarium(
    session_id: str, req: RenameRequest, engine=Depends(get_engine)
):
    try:
        sess = await asyncio.to_thread(
            lifecycle.rename_session, engine, session_id, req.name
        )
        return {"session_id": sess.session_id, "name": sess.name}
    except KeyError:
        raise HTTPException(404, f"session {session_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{session_id}/creatures/{creature_id}/rename")
async def rename_session_creature(
    session_id: str, creature_id: str, req: RenameRequest, engine=Depends(get_engine)
):
    try:
        return await asyncio.to_thread(
            lifecycle.rename_creature, engine, creature_id, req.name
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── unified session resolution / read ───────────────────────────────


def _resolve_session_sync(engine, identifier: str):
    """Return the live :class:`Session` for ``identifier``, accepting
    either a session_id (graph_id) or a creature_id. The runtime
    engine has no agent-vs-terrarium distinction; this resolver lets
    bookmarked URLs from before a graph grew past one member keep
    resolving to the same session without a forced redirect."""
    try:
        return lifecycle.get_session(engine, identifier)
    except KeyError:
        gid = lifecycle.find_session_for_creature(engine, identifier)
        if gid is not None:
            return lifecycle.get_session(engine, gid)
        raise


@router.delete("/agents/{creature_id}")
async def stop_creature_by_id(creature_id: str, engine=Depends(get_engine)):
    sid = await asyncio.to_thread(
        lifecycle.find_session_for_creature, engine, creature_id
    )
    if sid is None:
        raise HTTPException(404, f"Agent not found: {creature_id}")
    try:
        await lifecycle.stop_session(engine, sid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"status": "stopped"}


@router.delete("/terrariums/{session_id}")
async def stop_terrarium_session(session_id: str, engine=Depends(get_engine)):
    try:
        await lifecycle.stop_session(engine, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"status": "stopped"}


@router.get("/agents")
async def list_active_agents(engine=Depends(get_engine)):
    """Legacy alias — returns sessions whose graph holds exactly one
    creature (the original ``agent`` shape). Multi-creature sessions
    that grew via ``group_add_node`` migrate to the terrarium list."""
    return await asyncio.to_thread(_list_solo_legacy_sync, engine)


@router.get("/terrariums")
async def list_active_terrariums(engine=Depends(get_engine)):
    """Legacy alias — returns sessions whose graph holds 2+ creatures
    OR was created from a terrarium recipe."""
    return await asyncio.to_thread(_list_multi_legacy_sync, engine)


@router.get("/agents/{creature_id}")
async def get_creature_status(creature_id: str, engine=Depends(get_engine)):
    """Legacy ``/agents/{id}`` accessor. Accepts either a creature_id
    or a session_id and returns the unified session shape."""
    try:
        sess = await asyncio.to_thread(_resolve_session_sync, engine, creature_id)
    except KeyError:
        raise HTTPException(404, f"Agent not found: {creature_id}")
    return _session_legacy_agent_response(sess)


@router.get("/terrariums/{session_id}")
async def get_terrarium_session(session_id: str, engine=Depends(get_engine)):
    """Legacy ``/terrariums/{id}`` accessor. Accepts either a
    session_id or a creature_id and returns the unified session
    shape under the historical terrarium-style keys."""
    try:
        sess = await asyncio.to_thread(_resolve_session_sync, engine, session_id)
    except KeyError:
        raise HTTPException(404, f"Terrarium not found: {session_id}")
    return _session_legacy_terrarium_response(sess)


@router.get("")
async def list_active_sessions(engine=Depends(get_engine)):
    """Canonical list endpoint — every active session in the unified
    shape. Frontend stores prefer this over the legacy aliases."""
    sessions = await asyncio.to_thread(lifecycle.list_sessions, engine)
    return [s.to_dict() for s in sessions]


@router.get("/{session_id}")
async def get_active_session(session_id: str, engine=Depends(get_engine)):
    """Canonical session getter. Accepts either a session_id or a
    creature_id; both resolve to the same unified shape."""
    try:
        sess = await asyncio.to_thread(_resolve_session_sync, engine, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return sess.to_dict()


@router.delete("/{session_id}")
async def stop_active_session(session_id: str, engine=Depends(get_engine)):
    try:
        await lifecycle.stop_session(engine, session_id)
        return {"status": "stopped"}
    except KeyError as e:
        raise HTTPException(404, str(e))


# ─── per-session creature CRUD (hot-plug) ────────────────────────────


@router.get("/{session_id}/creatures")
async def list_session_creatures(session_id: str, engine=Depends(get_engine)):
    try:
        return await asyncio.to_thread(lifecycle.list_creatures, engine, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/{session_id}/creatures")
async def add_session_creature(
    session_id: str, req: CreatureAdd, engine=Depends(get_engine)
):
    cfg = CreatureConfig(
        name=req.name,
        config_path=req.config_path,
        listen_channels=req.listen_channels,
        send_channels=req.send_channels,
    )
    try:
        cid = await lifecycle.add_creature(engine, session_id, cfg)
        return {"creature_id": cid, "status": "running"}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@router.delete("/{session_id}/creatures/{creature_id}")
async def remove_session_creature(
    session_id: str, creature_id: str, engine=Depends(get_engine)
):
    try:
        removed = await lifecycle.remove_creature(engine, session_id, creature_id)
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


def _list_solo_legacy_sync(engine) -> list[dict]:
    """Sessions with exactly one creature, in legacy agent shape."""
    out: list[dict] = []
    for listing in lifecycle.list_sessions(engine):
        if listing.creatures != 1:
            continue
        full = lifecycle.get_session(engine, listing.session_id)
        if full.creatures:
            out.append(_session_legacy_agent_response(full))
    return out


def _list_multi_legacy_sync(engine) -> list[dict]:
    """Sessions with 2+ creatures (or recipe-loaded), in legacy
    terrarium shape."""
    out: list[dict] = []
    for listing in lifecycle.list_sessions(engine):
        if listing.creatures < 2:
            continue
        full = lifecycle.get_session(engine, listing.session_id)
        out.append(_session_legacy_terrarium_response(full))
    return out
