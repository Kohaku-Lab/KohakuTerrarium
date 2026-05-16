"""Sessions wiring — runtime ``config.output_wiring`` edges.

Service-driven: each op routes by creature ``_home`` in multi-node
mode.  Topology refresh events come from the engine layer; the route
no longer emits a second TOPOLOGY_CHANGED (was causing duplicate
events — fixed per kt-audit M3).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.terrarium.service import TerrariumService

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
    service: TerrariumService = Depends(get_service),
):
    """List direct output-wiring edges for a creature."""
    cid = await resolve_creature_id(service, creature_id)
    try:
        outputs = await service.list_output_wiring(cid)
        return {"outputs": outputs}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.post("/{session_id}/creatures/{creature_id}/outputs")
async def wire_creature_output(
    session_id: str,
    creature_id: str,
    req: OutputWirePayload,
    service: TerrariumService = Depends(get_service),
):
    """Add a direct output-wiring edge for a creature."""
    cid = await resolve_creature_id(service, creature_id)
    try:
        result = await service.wire_output(cid, req.as_entry())
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "status": "wired",
        "edge_id": result.get("edge_id", ""),
        "graph_id": session_id,
    }


@router.delete("/{session_id}/creatures/{creature_id}/outputs/{edge_id}")
async def unwire_creature_output(
    session_id: str,
    creature_id: str,
    edge_id: str,
    service: TerrariumService = Depends(get_service),
):
    """Detach a direct output-wiring edge."""
    cid = await resolve_creature_id(service, creature_id)
    try:
        ok = await service.unwire_output(cid, edge_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    return {"status": "unwired" if ok else "not_found"}


@router.get("/{session_id}/creatures/{creature_id}/sinks")
async def list_creature_sinks(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    """Return secondary-sink ids attached to a creature.

    No engine-level sink enumerator yet; this endpoint is kept for
    callers that only need to check creature existence.
    """
    cid = await resolve_creature_id(service, creature_id)
    try:
        info = await service.get_creature_info(cid)
        if info is None:
            raise KeyError(creature_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    return {"sinks": []}


@router.delete("/{session_id}/creatures/{creature_id}/sinks/{sink_id}")
async def unwire_sink(
    session_id: str,
    creature_id: str,
    sink_id: str,
    service: TerrariumService = Depends(get_service),
):
    """Detach a previously-wired secondary output sink."""
    cid = await resolve_creature_id(service, creature_id)
    try:
        ok = await service.unwire_output_sink(cid, sink_id)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    return {"status": "unwired" if ok else "not_found"}
