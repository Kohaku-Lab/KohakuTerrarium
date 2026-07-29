"""Manage runtime output wiring and secondary sinks for live creatures.

Creature-to-creature edges update the same ``config.output_wiring`` structure as
static configuration. Explicit sink helpers support lower-level I/O attachment
and WebSocket observation.
"""

from typing import Any

from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium import TerrariumService
from kohakuterrarium.terrarium.graph_identity import resolve_local_graph_target
from kohakuterrarium.studio._runtime import as_engine


async def wire_output(
    service: "TerrariumService",
    creature_id: str,
    target: str | dict[str, Any],
) -> str:
    """Add a runtime output edge resolved inside the source logical graph."""
    engine = as_engine(service)
    target_name = _extract_target_name(target)
    if not target_name or target_name == "root":
        return await engine.wire_output(creature_id, target)
    resolved = resolve_local_graph_target(
        engine._topology,
        engine._creatures,
        caller_id=creature_id,
        target=target_name,
    )
    canonical = dict(target) if isinstance(target, dict) else {}
    canonical["to"] = resolved.target_id
    return await engine.wire_output(creature_id, canonical)


def _extract_target_name(target: str | dict[str, Any]) -> str | None:
    if isinstance(target, str):
        return target
    if isinstance(target, dict):
        value = target.get("to")
        if isinstance(value, str) and value:
            return value
    return None


async def unwire_output(
    service: "TerrariumService", creature_id: str, edge_id: str
) -> bool:
    """Detach a previously-wired runtime output edge."""
    engine = as_engine(service)
    return await engine.unwire_output(creature_id, edge_id)


def list_output_wiring(
    service: "TerrariumService", creature_id: str
) -> list[dict[str, Any]]:
    """List runtime/static output-wiring edges for a creature."""
    engine = as_engine(service)
    return engine.list_output_wiring(creature_id)


async def wire_output_sink(
    service: "TerrariumService",
    creature_id: str,
    sink: OutputModule,
) -> str:
    """Attach a low-level secondary output sink to a creature."""
    engine = as_engine(service)
    return await engine.wire_output_sink(creature_id, sink)


async def unwire_output_sink(
    service: "TerrariumService", creature_id: str, sink_id: str
) -> bool:
    """Detach a previously-wired secondary sink."""
    engine = as_engine(service)
    return await engine.unwire_output_sink(creature_id, sink_id)
