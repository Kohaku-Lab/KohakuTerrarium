"""
Hot-plug demonstration on the Terrarium engine.

Starts two solo creatures in their own graphs, draws a channel
between them (which forces a graph merge plus environment + session
union), then disconnects them (which splits the graph back apart).
Real creatures, real channels, no mocks.
"""

import asyncio

from kohakuterrarium import EventFilter, EventKind, Terrarium


async def main() -> None:
    """Connect and disconnect two creatures while observing topology events."""
    async with Terrarium() as engine:
        alice = await engine.add_creature("@kt-biome/creatures/general")
        bob = await engine.add_creature("@kt-biome/creatures/general")
        print(
            f"[start] alice graph={alice.graph_id[:14]} "
            f"bob graph={bob.graph_id[:14]}"
        )

        events: list = []

        async def watch():
            """Collect topology changes emitted during the demonstration."""
            async for ev in engine.subscribe(
                EventFilter(kinds={EventKind.TOPOLOGY_CHANGED})
            ):
                events.append(ev)

        watcher = asyncio.create_task(watch())
        await asyncio.sleep(0)

        # Connecting singleton graphs merges topology, environment, and sessions.
        result = await engine.connect(alice, bob, channel="alice_to_bob")
        print(
            f"[merge] result={result.delta_kind} channel={result.channel} "
            f"alice graph={alice.graph_id[:14]} "
            f"bob graph={bob.graph_id[:14]}"
        )

        # Removing the only bridge splits the merged graph back into singletons.
        d = await engine.disconnect(alice, bob, channel="alice_to_bob")
        print(
            f"[split] result={d.delta_kind} channels={d.channels} "
            f"alice graph={alice.graph_id[:14]} "
            f"bob graph={bob.graph_id[:14]}"
        )

        await asyncio.sleep(0.05)
        watcher.cancel()
        for ev in events:
            print(f"  -> topology_changed: {ev.payload.get('kind')}")


if __name__ == "__main__":
    asyncio.run(main())
