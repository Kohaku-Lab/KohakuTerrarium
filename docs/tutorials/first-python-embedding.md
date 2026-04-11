# First Python Embedding

This tutorial walks through the smallest useful way to use KohakuTerrarium as a Python library.

By the end, you will:

- create an agent from Python
- send it input programmatically
- understand when to use Python embedding instead of `kt run`

## Why embed instead of using the CLI

Use Python embedding when:

- your application owns the orchestration
- the agent is part of a server, bot, or app
- you want to mix agent work with normal Python logic
- you want tighter control than the CLI loop gives you

## Step 1: create a script

Create `demo.py`:

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    agent = Agent.from_path("@kt-defaults/creatures/general")

    await agent.start()
    try:
        await agent.inject_input("Summarize what a terrarium is in this framework.")
    finally:
        await agent.stop()


asyncio.run(main())
```

Run it:

```bash
python demo.py
```

## Step 2: understand the lifecycle

There are three important steps here:

1. `Agent.from_path(...)` builds the creature from config
2. `start()` initializes the runtime
3. `inject_input(...)` sends a message without using CLI input
4. `stop()` cleans everything up

This is the simplest embedded lifecycle.

## Step 3: capture output yourself

If you want to collect output in your own application instead of letting the default output surface handle it, attach your own handler.

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    parts: list[str] = []

    agent = Agent.from_path("@kt-defaults/creatures/general")
    agent.set_output_handler(lambda text: parts.append(text), replace_default=True)

    await agent.start()
    try:
        await agent.inject_input("What is the difference between a creature and a terrarium?")
    finally:
        await agent.stop()

    print("".join(parts))


asyncio.run(main())
```

This is a good pattern for:

- web backends
- bots
- application-specific rendering

## Step 4: know when to move up a level

If your application is managing many agents or terrariums, move up to higher-level APIs:

- `TerrariumRuntime` for direct multi-agent runtime control
- `KohakuManager` for service-style management

## What you learned

You learned the key mental shift of programmatic use:

- with `kt run`, the creature runs itself
- with Python embedding, your code runs the creature

That difference is the foundation of the programmatic side of the framework.

## Next steps

- [Programmatic Usage](../guides/programmatic-usage.md)
- [Python API](../reference/python.md)
- [Composition Algebra](../concepts/composition-algebra.md)
