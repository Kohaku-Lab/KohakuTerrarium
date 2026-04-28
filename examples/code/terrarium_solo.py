"""
Single creature via the unified Terrarium engine.

The same engine that hosts multi-creature graphs hosts standalone
agents — they're just creatures in a 1-creature graph.  This is the
direct programmatic replacement for ``AgentSession.from_path`` once
the engine cutover lands.
"""

import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    engine, alice = await Terrarium.with_creature("@kt-biome/creatures/general")
    try:
        questions = [
            "What is a terrarium?",
            "How does a multi-agent system differ from a single agent?",
        ]
        for q in questions:
            print(f"\nQ: {q}")
            print("A: ", end="", flush=True)
            async for chunk in alice.chat(q):
                print(chunk, end="", flush=True)
            print()
        print(f"\n[status] running={alice.is_running} graph={alice.graph_id}")
    finally:
        await engine.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
