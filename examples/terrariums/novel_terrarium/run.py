"""Run the novel writer terrarium on the engine and seed it with a story prompt."""

import asyncio
import sys
from pathlib import Path

from kohakuterrarium import Terrarium
from kohakuterrarium.core.channel import ChannelMessage

DEFAULT_SEED = "A story about a lighthouse keeper"
RUN_SECONDS = 600


async def main() -> None:
    """Load the recipe, send the seed on its channel, and let the creatures work."""
    seed = " ".join(sys.argv[1:]) or DEFAULT_SEED
    output_dir = Path.cwd() / "example_output" / "novel_terrarium"
    output_dir.mkdir(parents=True, exist_ok=True)

    engine = await Terrarium.from_recipe(
        str(Path(__file__).parent), pwd=str(output_dir)
    )
    try:
        graph_id = next(iter(engine.list_graphs())).graph_id
        print(f"Creatures: {[c.name for c in engine.list_creatures()]}")
        print(f"Output dir: {output_dir}")
        await engine.channel(graph_id, "seed").send(
            ChannelMessage(sender="user", content=seed)
        )
        print(f"Seeded: {seed!r}")
        await asyncio.sleep(RUN_SECONDS)
    finally:
        await engine.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
