"""Active sessions — engine-backed lifecycle endpoints.

Mounted at ``/api/sessions/active``.  Replaces the legacy ``/api/agents``
and ``/api/terrariums`` create/list/get/stop endpoints with one URL
shape per the Phase 2 plan (``§6 URL contract``).
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.schemas import AgentCreate, CreatureAdd, TerrariumCreate
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.terrarium.config import CreatureConfig

router = APIRouter()


class CreaturePayload(BaseModel):
    """Body for ``POST /api/sessions/active/creature``."""

    config_path: str
    llm: str | None = None
    pwd: str | None = None


@router.post("/creature")
async def create_creature_session(req: CreaturePayload, engine=Depends(get_engine)):
    """Start a 1-creature session.  Returns the new session handle."""
    try:
        session = await lifecycle.start_creature(
            engine,
            config_path=req.config_path,
            llm_override=req.llm,
            pwd=req.pwd,
        )
        return {**session.to_dict(), "status": "running"}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@router.post("/terrarium")
async def create_terrarium_session(req: TerrariumCreate, engine=Depends(get_engine)):
    """Start a multi-creature terrarium session from a recipe."""
    try:
        session = await lifecycle.start_terrarium(
            engine, config_path=req.config_path, pwd=req.pwd
        )
        return {**session.to_dict(), "status": "running"}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


# ── Legacy compat aliases — frontend uses these ──────────────────────


@router.post("/agents")
async def create_agent_compat(req: AgentCreate, engine=Depends(get_engine)):
    """Legacy alias kept for the ``agentAPI.create`` frontend path."""
    try:
        session = await lifecycle.start_creature(
            engine,
            config_path=req.config_path,
            llm_override=req.llm,
            pwd=req.pwd,
        )
        # Frontend reads ``agent_id`` (== creature_id) for routing.
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
    """Legacy alias kept for the ``terrariumAPI.create`` frontend path."""
    try:
        session = await lifecycle.start_terrarium(
            engine, config_path=req.config_path, pwd=req.pwd
        )
        return {"terrarium_id": session.session_id, "status": "running"}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@router.get("/agents/{creature_id}")
async def get_creature_status(creature_id: str, engine=Depends(get_engine)):
    """Look up a creature by id; returns the legacy agent-shape status."""
    try:
        return engine.get_creature(creature_id).get_status()
    except KeyError:
        raise HTTPException(404, f"Agent not found: {creature_id}")


@router.delete("/agents/{creature_id}")
async def stop_creature_by_id(creature_id: str, engine=Depends(get_engine)):
    """Stop a creature by id — drops the surrounding session."""
    sid = lifecycle.find_session_for_creature(engine, creature_id)
    if sid is None:
        raise HTTPException(404, f"Agent not found: {creature_id}")
    try:
        await lifecycle.stop_session(engine, sid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"status": "stopped"}


@router.get("/terrariums/{session_id}")
async def get_terrarium_session(session_id: str, engine=Depends(get_engine)):
    """Look up a terrarium session by id; returns legacy terrarium shape.

    404s for creature sessions so the frontend's "probe terrarium then
    fall back to agent" path can correctly route a single-creature
    resume to the agent panel.
    """
    try:
        sess = lifecycle.get_session(engine, session_id)
    except KeyError:
        raise HTTPException(404, f"Terrarium not found: {session_id}")
    if sess.kind != "terrarium":
        raise HTTPException(404, f"Terrarium not found: {session_id}")
    return {
        "terrarium_id": sess.session_id,
        "name": sess.name,
        "running": True,
        "creatures": {
            c.get("name", c.get("creature_id", "")): c for c in sess.creatures
        },
        "channels": sess.channels,
        "has_root": sess.has_root,
    }


@router.delete("/terrariums/{session_id}")
async def stop_terrarium_session(session_id: str, engine=Depends(get_engine)):
    """Stop a terrarium session."""
    try:
        await lifecycle.stop_session(engine, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"status": "stopped"}


@router.get("/agents")
async def list_active_agents(engine=Depends(get_engine)):
    """List standalone (1-creature) sessions in legacy agent shape."""
    out: list[dict] = []
    for sess in lifecycle.list_sessions(engine):
        if sess.kind != "creature":
            continue
        full = lifecycle.get_session(engine, sess.session_id)
        if full.creatures:
            out.append(full.creatures[0])
    return out


@router.get("/terrariums")
async def list_active_terrariums(engine=Depends(get_engine)):
    """List terrarium sessions in legacy terrarium shape."""
    out: list[dict] = []
    for sess in lifecycle.list_sessions(engine):
        if sess.kind != "terrarium":
            continue
        full = lifecycle.get_session(engine, sess.session_id)
        out.append(
            {
                "terrarium_id": full.session_id,
                "name": full.name,
                "running": True,
                "creatures": {
                    c.get("name", c.get("creature_id", "")): c for c in full.creatures
                },
                "channels": full.channels,
                "has_root": full.has_root,
            }
        )
    return out


@router.get("")
async def list_active_sessions(engine=Depends(get_engine)):
    """List every active session (creature + terrarium)."""
    return [s.to_dict() for s in lifecycle.list_sessions(engine)]


@router.get("/{session_id}")
async def get_active_session(session_id: str, engine=Depends(get_engine)):
    """Get the full handle for one active session."""
    try:
        return lifecycle.get_session(engine, session_id).to_dict()
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.delete("/{session_id}")
async def stop_active_session(session_id: str, engine=Depends(get_engine)):
    """Stop and dispose an active session."""
    try:
        await lifecycle.stop_session(engine, session_id)
        return {"status": "stopped"}
    except KeyError as e:
        raise HTTPException(404, str(e))


# ── Per-session creature CRUD (hot-plug) ─────────────────────────────


@router.get("/{session_id}/creatures")
async def list_session_creatures(session_id: str, engine=Depends(get_engine)):
    """List every creature currently in the session."""
    try:
        return lifecycle.list_creatures(engine, session_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/{session_id}/creatures")
async def add_session_creature(
    session_id: str, req: CreatureAdd, engine=Depends(get_engine)
):
    """Hot-plug a creature into a running session."""
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
    """Remove a creature from a running session."""
    try:
        removed = await lifecycle.remove_creature(engine, session_id, creature_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if not removed:
        raise HTTPException(404, f"creature {creature_id!r} not found in session")
    return {"status": "removed"}
