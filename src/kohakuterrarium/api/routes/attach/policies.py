"""Attach policy hint route.

Exposes the studio :mod:`kohakuterrarium.studio.attach.policies` helpers
over HTTP so the frontend Inspector Overview can render an "IO bindings"
hint for any running target.

The frontend treats these endpoints as **informational hints**, not as
gating mechanisms: every running target offers Chat and Inspector tabs
regardless of policy. Hence the routes return 404 (rather than a typed
error) when the target is not currently live — the frontend silently
omits the hint line.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.attach import policies as policy_lib

router = APIRouter()


@router.get("/policies/{creature_id}")
async def get_creature_policies(
    creature_id: str, engine=Depends(get_engine)
) -> dict[str, list[str]]:
    """Return the attach policies a single creature supports.

    Returns ``{"policies": ["log", "trace", ...]}`` — order-stable list
    of short codes from :class:`policy_lib.Policy`.
    """
    try:
        engine.get_creature(creature_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found") from None
    policies = policy_lib.get_creature_policies(engine, creature_id)
    return {"policies": [p.value for p in policies]}


@router.get("/session_policies/{session_id}")
async def get_session_policies(
    session_id: str, engine=Depends(get_engine)
) -> dict[str, list[str]]:
    """Return the attach policies a whole session (graph) supports."""
    try:
        engine.get_graph(session_id)
    except KeyError:
        raise HTTPException(404, f"session {session_id!r} not found") from None
    policies = policy_lib.get_session_policies(engine, session_id)
    return {"policies": [p.value for p in policies]}
