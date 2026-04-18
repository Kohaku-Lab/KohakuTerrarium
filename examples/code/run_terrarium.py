"""
Run a terrarium — start a multi-agent team and observe channel traffic.

Shows how to start a terrarium, inject a seed prompt, and observe
the channel messages flowing between creatures.
"""

import asyncio

from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime


async def main() -> None:
    config = load_terrarium_config("@kt-biome/terrariums/swe_team")
    runtime = TerrariumRuntime(config)
    await runtime.start()

    # Inject a task into the tasks channel
    tasks_channel = runtime.environment.shared_channels.get("tasks")
    if tasks_channel:
        msg = ChannelMessage(
            sender="user",
            content="Fix the off-by-one error in src/pagination.py",
        )
        await tasks_channel.send(msg)
        print(f"Injected task: {msg.content}")

    try:
        await runtime.run()
    except KeyboardInterrupt:
        print("\nStopping terrarium...")
    finally:
        await runtime.stop()

    # Print final status
    status = runtime.get_status()
    print(f"\nTerrarium '{status['name']}' finished.")
    for name, info in status.get("creatures", {}).items():
        print(f"  {name}: running={info['running']}")


if __name__ == "__main__":
    asyncio.run(main())
