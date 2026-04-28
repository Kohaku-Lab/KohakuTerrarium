"""Sessions wiring — secondary output sinks.

Mounted at ``/api/sessions/wiring``.  Lightweight HTTP wrapper around
:mod:`studio.sessions.wiring`; the live IO attach lives on the WS
side under ``/ws/sessions/{sid}/creatures/{cid}/chat``.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.sessions import wiring as wiring_lib

router = APIRouter()


@router.get("/{session_id}/creatures/{creature_id}/sinks")
async def list_creature_sinks(
    session_id: str, creature_id: str, engine=Depends(get_engine)
):
    """Return secondary-sink ids attached to a creature.

    There is no engine-level enumerator yet, so this returns an empty
    placeholder.  Step 11's IO attach owns the live wiring path.
    """
    try:
        engine.get_creature(creature_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    return {"sinks": []}


@router.delete("/{session_id}/creatures/{creature_id}/sinks/{sink_id}")
async def unwire_sink(
    session_id: str,
    creature_id: str,
    sink_id: str,
    engine=Depends(get_engine),
):
    """Detach a previously-wired secondary output sink."""
    try:
        ok = await wiring_lib.unwire_output(engine, creature_id, sink_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    return {"status": "unwired" if ok else "not_found"}
