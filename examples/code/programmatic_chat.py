"""
Programmatic chat — the simplest agent-as-library pattern.

When your application needs to send a message and get a response —
not run an interactive loop. This is the baseline for all embedding
use cases: your code controls when the agent speaks.

AgentSession wraps Agent with streaming: send a message, iterate
chunks. This is what the web API uses internally.
"""

import asyncio

from kohakuterrarium.serving.agent_session import AgentSession


async def main() -> None:
    session = await AgentSession.from_path("@kt-biome/creatures/general")

    try:
        # Your code decides when to call the agent
        questions = [
            "What is a terrarium?",
            "How would you build one for tropical plants?",
        ]
        for q in questions:
            print(f"\nQ: {q}")
            print("A: ", end="", flush=True)
            async for chunk in session.chat(q):
                print(chunk, end="", flush=True)
            print()

    finally:
        await session.stop()


if __name__ == "__main__":
    asyncio.run(main())
