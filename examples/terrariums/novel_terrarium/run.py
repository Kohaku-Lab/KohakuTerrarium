"""Run the novel writer terrarium."""

import asyncio
import os
import sys

# Resolve imports and environment relative to the repository checkout.
project_root = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(project_root, "src"))

from dotenv import load_dotenv

load_dotenv(os.path.join(project_root, ".env"))

from kohakuterrarium.terrarium import TerrariumRuntime, load_terrarium_config


async def main() -> None:
    """Load the novel recipe and run it from a dedicated output directory."""
    # Isolate generated novel files from the recipe and source tree.
    output_dir = os.path.join(project_root, "example_output", "novel_terrarium")
    os.makedirs(output_dir, exist_ok=True)
    os.chdir(output_dir)

    config_path = os.path.dirname(__file__)
    config = load_terrarium_config(config_path)

    print(f"=== Terrarium: {config.name} ===")
    print(f"Creatures: {[c.name for c in config.creatures]}")
    print(f"Channels: {[c.name for c in config.channels]}")
    print(f"Output dir: {output_dir}")
    print()

    runtime = TerrariumRuntime(config)
    await runtime.run()


if __name__ == "__main__":
    asyncio.run(main())
