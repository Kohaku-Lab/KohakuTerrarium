"""
Programmatic chat — the simplest agent-as-library pattern.

When your application needs to send a message and get a response —
not run an interactive loop. ``Agent.build`` constructs an agent from
any config (path, ``@pkg/...`` ref, or AgentConfig); ``run`` drives one
turn and returns a typed TurnResult; ``run_stream`` yields events live.
"""

import asyncio

from kohakuterrarium import Agent, TextChunk, TurnEnded


async def main() -> None:
    agent = await Agent.build("@kt-biome/creatures/general")
    await agent.start()

    try:
        # One buffered turn: TurnResult carries status / text / usage.
        result = await agent.run("What is a terrarium?", timeout=300)
        print(f"Q: What is a terrarium?\nA: {result.text}")
        if result.usage:
            print(f"   [{result.usage.get('total_tokens', '?')} tokens]")

        # One streamed turn: typed events as they happen.
        print("\nQ: How would you build one for tropical plants?\nA: ", end="")
        async for event in agent.run_stream(
            "How would you build one for tropical plants?"
        ):
            if isinstance(event, TextChunk):
                print(event.text, end="", flush=True)
            elif isinstance(event, TurnEnded):
                print(f"\n   [turn status: {event.result.status}]")

    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
