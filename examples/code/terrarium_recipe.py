"""
Load a terrarium recipe via the unified engine.

``Terrarium.from_recipe`` is the engine-level equivalent of the legacy
``TerrariumRuntime(config).start()``.  It walks the YAML recipe, adds
every creature to a single graph, declares channels, and wires
listen/send edges so messages flow.
"""

import asyncio

from kohakuterrarium import EventFilter, EventKind, Terrarium
from kohakuterrarium.core.channel import ChannelMessage


async def main() -> None:
    """Load a recipe, seed a channel, and observe representative engine events."""
    engine = await Terrarium.from_recipe("@kt-biome/terrariums/swe_team")
    try:
        graph_id = next(iter(engine.list_graphs())).graph_id
        print(f"[recipe] graph {graph_id[:14]} hosts " f"{len(engine)} creatures")

        # Recipe-declared channels are available through the live graph handle.
        tasks = engine.channel(graph_id, "tasks")
        if tasks is not None:
            await tasks.send(
                ChannelMessage(
                    sender="user",
                    content="Fix the off-by-one error in src/pagination.py",
                )
            )
            print("[seed] task injected on 'tasks' channel")

        # Subscription filters keep the demonstration focused on lifecycle events.
        seen = 0

        async def watch():
            """Count selected engine events until the demonstration ends."""
            nonlocal seen
            async for ev in engine.subscribe(
                EventFilter(
                    kinds={
                        EventKind.CREATURE_STARTED,
                        EventKind.TOPOLOGY_CHANGED,
                    }
                )
            ):
                print(f"  -> {ev.kind.value} creature={ev.creature_id}")
                seen += 1

        watcher = asyncio.create_task(watch())
        await asyncio.sleep(3.0)
        watcher.cancel()
        print(f"[done] observed {seen} engine events")
    finally:
        await engine.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
