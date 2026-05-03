"""Sessions wiring — runtime ``config.output_wiring`` edges.

Mounted at ``/api/sessions/wiring``.  This is the graph-editor-facing
surface for direct turn-output routing between live creatures.  The live
IO attach still owns websocket secondary sinks under the attach routes.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.sessions import wiring as wiring_lib
from kohakuterrarium.terrarium.events import EngineEvent, EventKind

router = APIRouter()


class OutputWirePayload(BaseModel):
    """Body for adding one runtime output-wiring edge."""

    to: str
    with_content: bool = True
    prompt: str | None = None
    prompt_format: str = "simple"
    allow_self_trigger: bool = False

    def as_entry(self) -> dict[str, Any]:
        return {
            "to": self.to,
            "with_content": self.with_content,
            "prompt": self.prompt,
            "prompt_format": self.prompt_format,
            "allow_self_trigger": self.allow_self_trigger,
        }


@router.get("/{session_id}/creatures/{creature_id}/outputs")
async def list_creature_outputs(
    session_id: str,
    creature_id: str,
    engine=Depends(get_engine),
):
    """List direct output-wiring edges for a creature."""
    try:
        return {"outputs": wiring_lib.list_output_wiring(engine, creature_id)}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.post("/{session_id}/creatures/{creature_id}/outputs")
async def wire_creature_output(
    session_id: str,
    creature_id: str,
    req: OutputWirePayload,
    engine=Depends(get_engine),
):
    """Add a direct output-wiring edge for a creature."""
    try:
        edge_id = await wiring_lib.wire_output(engine, creature_id, req.as_entry())
        # Cross-graph wiring may have merged the source's graph into
        # another. Look up the current graph id rather than reusing the
        # URL's session_id so the WS event lands on the surviving
        # molecule.
        live_graph_id = _live_graph_id(engine, creature_id, session_id)
        _emit_output_wiring_changed(
            engine, live_graph_id, creature_id, edge_id, "wired"
        )
        return {"status": "wired", "edge_id": edge_id, "graph_id": live_graph_id}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/{session_id}/creatures/{creature_id}/outputs/{edge_id}")
async def unwire_creature_output(
    session_id: str,
    creature_id: str,
    edge_id: str,
    engine=Depends(get_engine),
):
    """Detach a direct output-wiring edge."""
    try:
        ok = await wiring_lib.unwire_output(engine, creature_id, edge_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    if ok:
        _emit_output_wiring_changed(engine, session_id, creature_id, edge_id, "unwired")
    return {"status": "unwired" if ok else "not_found"}


@router.get("/{session_id}/creatures/{creature_id}/sinks")
async def list_creature_sinks(
    session_id: str,
    creature_id: str,
    engine=Depends(get_engine),
):
    """Return secondary-sink ids attached to a creature.

    There is still no engine-level sink enumerator; this endpoint is
    kept for callers that only need to check creature existence.
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
        ok = await wiring_lib.unwire_output_sink(engine, creature_id, sink_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    return {"status": "unwired" if ok else "not_found"}


def _live_graph_id(engine, creature_id: str, fallback: str) -> str:
    try:
        creature = engine.get_creature(creature_id)
    except KeyError:
        return fallback
    return getattr(creature, "graph_id", "") or fallback


def _emit_output_wiring_changed(
    engine,
    session_id: str,
    creature_id: str,
    edge_id: str,
    status: str,
) -> None:
    emit = getattr(engine, "_emit", None)
    if not callable(emit):
        return
    emit(
        EngineEvent(
            kind=EventKind.TOPOLOGY_CHANGED,
            graph_id=session_id,
            creature_id=creature_id,
            payload={"kind": "output_wiring", "edge_id": edge_id, "status": status},
        )
    )
