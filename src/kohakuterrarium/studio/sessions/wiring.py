"""Engine-backed output wiring between live creatures.

``wire_output`` / ``unwire_output`` mutate the same per-agent
``config.output_wiring`` list that static creature configs use.  The
lower-level secondary-output-sink helpers stay available for IO attach
and websocket observers under explicit ``*_sink`` names.
"""

from typing import Any

import kohakuterrarium.terrarium.channels as _channels
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium.engine import Terrarium


async def wire_output(
    engine: Terrarium,
    creature_id: str,
    target: str | dict[str, Any],
) -> str:
    """Add a runtime ``config.output_wiring`` edge from a creature.

    If the target resolves to a creature in a different graph the two
    graphs are merged first — output wiring is dispatched per-graph at
    emit time, so without the merge the resolver would silently drop
    every emission. The merge is the engine's "ensure same graph"
    primitive, which does *not* introduce a channel side-effect.
    """
    target_name = _extract_target_name(target)
    if target_name and target_name != "root":
        await _ensure_target_in_same_graph(engine, creature_id, target_name)
    return await engine.wire_output(creature_id, target)


def _extract_target_name(target: str | dict[str, Any]) -> str | None:
    if isinstance(target, str):
        return target
    if isinstance(target, dict):
        value = target.get("to")
        if isinstance(value, str) and value:
            return value
    return None


async def _ensure_target_in_same_graph(
    engine: Terrarium,
    source_id: str,
    target_name: str,
) -> None:
    try:
        source = engine.get_creature(source_id)
    except KeyError:
        return
    target = _resolve_creature_by_name(engine, target_name)
    if target is None:
        return
    if target.graph_id == source.graph_id:
        return
    await _channels.ensure_same_graph(engine, source_id, target.creature_id)


def _resolve_creature_by_name(engine: Terrarium, name: str):
    """Look up a creature by id, then by display name as a fallback.

    Output-wiring entries can target either a creature id or its
    config-level name; the live resolver checks both at emit time so
    the cross-graph merge has to mirror that lookup or we'd refuse to
    merge for the very wirings the engine would happily honor.
    """
    try:
        return engine.get_creature(name)
    except KeyError:
        pass
    for handle in getattr(engine, "_creatures", {}).values():
        if getattr(handle, "name", None) == name:
            return handle
    return None


async def unwire_output(engine: Terrarium, creature_id: str, edge_id: str) -> bool:
    """Detach a previously-wired runtime output edge."""
    return await engine.unwire_output(creature_id, edge_id)


def list_output_wiring(engine: Terrarium, creature_id: str) -> list[dict[str, Any]]:
    """List runtime/static output-wiring edges for a creature."""
    return engine.list_output_wiring(creature_id)


async def wire_output_sink(
    engine: Terrarium,
    creature_id: str,
    sink: OutputModule,
) -> str:
    """Attach a low-level secondary output sink to a creature."""
    return await engine.wire_output_sink(creature_id, sink)


async def unwire_output_sink(engine: Terrarium, creature_id: str, sink_id: str) -> bool:
    """Detach a previously-wired secondary sink."""
    return await engine.unwire_output_sink(creature_id, sink_id)
