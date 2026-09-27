"""Unload a whole session graph without deleting its persisted membership."""

import asyncio
from typing import TYPE_CHECKING

import kohakuterrarium.terrarium.graph_checkpoint as checkpoint
import kohakuterrarium.terrarium.wiring as wiring
from kohakuterrarium.terrarium.channels import remove_channel_trigger
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.terrarium.graph_manifest import MANIFEST_KEY

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium


async def unload_session_graph(engine: "Terrarium", graph_id: str) -> tuple[str, ...]:
    """Unload runtime membership, leaving the saved session open for its owner."""
    graph = engine._topology.graphs.get(graph_id)
    if graph is None:
        raise KeyError(f"graph {graph_id!r} not in engine")
    creature_ids = tuple(sorted(graph.creature_ids))
    stops = []
    if engine._drive_runtime is not None:
        stops.append(engine._drive_runtime.detach_graph(graph_id))
    for creature_id in creature_ids:
        creature = engine._creatures[creature_id]
        stops.append(creature.stop(requested=False))
    results = await asyncio.gather(*stops, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    if (
        engine._topology.graphs.get(graph_id) is not graph
        or set(creature_ids) != graph.creature_ids
    ):
        raise RuntimeError("Session topology changed during unload; retry stopping it")

    store = engine._session_stores.get(graph_id)
    previous_manifest = store.meta.get(MANIFEST_KEY) if store is not None else None
    persisted = await checkpoint.checkpoint(engine, graph_id)
    if store is not None and creature_ids and not persisted:
        if previous_manifest is not None:
            store.meta[MANIFEST_KEY] = previous_manifest
            store.checkpoint()
        raise RuntimeError("Cannot unload a session without a persisted graph manifest")
    if (
        engine._topology.graphs.get(graph_id) is not graph
        or set(creature_ids) != graph.creature_ids
    ):
        raise RuntimeError(
            "Session topology changed during checkpoint; retry stopping it"
        )
    if store is not None:
        store.checkpoint()

    for creature_id in creature_ids:
        creature = engine._creatures[creature_id]
        for channel_name in list(creature.listen_channels):
            remove_channel_trigger(
                creature.agent, subscriber_id=creature.name, channel_name=channel_name
            )
        engine._creatures.pop(creature_id, None)
        engine._topology.creature_to_graph.pop(creature_id, None)
    engine._topology.graphs.pop(graph_id, None)
    engine._environments.pop(graph_id, None)
    engine._session_stores.pop(graph_id, None)
    engine._owned_sessions.discard(graph_id)
    engine._recipe_graph_locks.pop(graph_id, None)
    getattr(engine, "_topology_replay_leftovers", {}).pop(graph_id, None)
    checkpoint.discard(engine, graph_id)
    wiring.install_output_wiring_resolver(engine)
    for creature_id in creature_ids:
        engine._emit(
            EngineEvent(
                kind=EventKind.CREATURE_STOPPED,
                creature_id=creature_id,
                graph_id=graph_id,
            )
        )
    return creature_ids
