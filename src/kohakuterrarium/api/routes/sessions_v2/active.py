"""Active sessions — engine-backed lifecycle endpoints.

Mounted at ``/api/sessions/active``.  Replaces the legacy ``/api/agents``
and ``/api/terrariums`` create/list/get/stop endpoints with one URL
shape per the Phase 2 plan (``§6 URL contract``).
"""

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
            name=req.name,
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
            engine, config_path=req.config_path, pwd=req.pwd, name=req.name
        )
        return {"terrarium_id": session.session_id, "status": "running"}
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))


@router.post("/agents/{creature_id}/rename")
async def rename_agent(
    creature_id: str, req: RenameRequest, engine=Depends(get_engine)
):
    """Rename a standalone creature (mirrors the meta name on the
    session).  The creature_id stays stable; only the display name
    changes."""
    try:
        return lifecycle.rename_creature(engine, creature_id, req.name)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/terrariums/{session_id}/rename")
async def rename_terrarium(
    session_id: str, req: RenameRequest, engine=Depends(get_engine)
):
    """Rename a terrarium session's display label.  Creature names
    inside the recipe stay untouched."""
    try:
        sess = lifecycle.rename_session(engine, session_id, req.name)
        return {"session_id": sess.session_id, "name": sess.name}
    except KeyError:
        raise HTTPException(404, f"session {session_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{session_id}/creatures/{creature_id}/rename")
async def rename_session_creature(
    session_id: str, creature_id: str, req: RenameRequest, engine=Depends(get_engine)
):
    """Rename a creature that lives inside a multi-creature session.

    Used by the graph editor's per-card "Rename" action — kept as a
    distinct route from the standalone-agent rename so callers can
    address creatures within a terrarium recipe by id without having
    to first resolve which graph they belong to.
    """
    try:
        return lifecycle.rename_creature(engine, creature_id, req.name)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
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
    return _terrarium_response(sess)


def _terrarium_response(sess) -> dict:
    """Shape a terrarium :class:`Session` into the legacy wire format.

    The frontend's ``stores/instances._mapTerrarium`` reads ``root_model``
    / ``root_llm_name`` / ``root_session_id`` / ``root_max_context`` /
    ``root_compact_threshold`` and a flat ``pwd``. The engine-backed
    handle stores the root creature in ``sess.creatures`` like any peer
    so the dispatcher gets a uniform shape — which means we have to
    pull those fields back out at the API edge or the inspector pill
    keeps showing a blank model for root.
    """
    creatures = {c.get("name", c.get("creature_id", "")): c for c in sess.creatures}
    root_status: dict = {}
    if sess.has_root:
        # Recipe-loaded terrariums always name the root creature "root".
        # Fall back to the first creature flagged ``is_root`` if a
        # custom recipe ever changes the name (no current path does).
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
        out.append(_terrarium_response(full))
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
