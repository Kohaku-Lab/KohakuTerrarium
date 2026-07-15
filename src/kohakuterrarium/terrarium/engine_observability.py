"""Event subscription and status helpers for the Terrarium engine."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from kohakuterrarium.terrarium.events import EngineEvent, EventFilter

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.creature_host import Creature
    from kohakuterrarium.terrarium.engine import Terrarium


@dataclass
class Subscriber:
    """Internal state for one engine event subscriber."""

    filter: EventFilter | None = None
    queue: asyncio.Queue[EngineEvent | None] = field(default_factory=asyncio.Queue)


async def subscription_iter(
    engine: "Terrarium", subscriber: Subscriber
) -> AsyncIterator[EngineEvent]:
    """Yield buffered events until shutdown and then unregister the subscriber."""
    try:
        while True:
            event = await subscriber.queue.get()
            if event is None:
                return
            yield event
    finally:
        try:
            engine._subscribers.remove(subscriber)
        except ValueError:
            pass


def status(engine: "Terrarium", creature: "Creature | str | None" = None) -> dict:
    """Return one creature's status or the engine-wide status roll-up."""
    if creature is not None:
        return engine._creature(creature).get_status()
    return {
        "running": engine._running,
        "creatures": {
            creature_id: current.get_status()
            for creature_id, current in engine._creatures.items()
        },
        "graphs": {
            graph_id: {
                "creature_ids": sorted(graph.creature_ids),
                "channels": sorted(graph.channels),
            }
            for graph_id, graph in engine._topology.graphs.items()
        },
    }


def emit(engine: "Terrarium", event: EngineEvent) -> None:
    """Fan out an event to every subscriber whose filter matches."""
    for subscriber in list(engine._subscribers):
        if subscriber.filter is None or subscriber.filter.matches(event):
            try:
                subscriber.queue.put_nowait(event)
            except Exception:  # pragma: no cover - defensive
                pass
