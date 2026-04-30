---
title: Embedding in Python
summary: Run an agent inside your own Python code via Agent, Creature, Terrarium, Studio, and the compose algebra.
tags:
  - tutorials
  - python
  - embedding
---

# First Python Embedding

**Problem:** you want to run a creature from inside your own Python
application — capture its output, drive its input from code, compose it
with other code.

**End state:** a minimal script that starts a creature through `Terrarium`,
streams output with `Creature.chat()`, embeds a multi-creature terrarium, and
uses Studio for session management. A lower-level `Agent` example is included
for custom output handlers.

**Prerequisites:** [First Creature](first-creature.md). You need the
package installed in a mode where you can `import kohakuterrarium`.

An agent in this framework is not a config — it is a Python object. A
config describes one; `Terrarium.with_creature(...)` builds an engine plus a
running `Creature` handle that you own. Sub-agents, `Terrarium` engines,
`Creature` handles, and `Studio` sessions are the same shape. See
[agent-as-python-object](../concepts/python-native/agent-as-python-object.md)
for the full mental model.

## Step 1 — Install editable

Goal: have `kohakuterrarium` importable from your venv.

From the repo root:

```bash
uv pip install -e .[dev]
```

The `[dev]` extras bring in the testing helpers you may want later.

## Step 2 — Minimal embed with `Creature.chat()`

Goal: build a running creature, send it one input, stream its response, and shut
the engine down cleanly.

`demo.py`:

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    engine, creature = await Terrarium.with_creature(
        "@kt-biome/creatures/general"
    )

    try:
        async for chunk in creature.chat(
            "In one sentence, what is a creature in KohakuTerrarium?"
        ):
            print(chunk, end="", flush=True)
        print()
    finally:
        await engine.shutdown()


asyncio.run(main())
```

Run it:

```bash
python demo.py
```

Three things to notice:

1. `Terrarium.with_creature` resolves `@kt-biome/...` the same way the CLI does.
2. A solo creature is still hosted by the same engine used for multi-creature graphs.
3. `Creature.chat(...)` is an async iterator of text chunks.

## Step 3 — Push input without draining output

Goal: feed a creature from your own scheduler, bot, or event loop. Use
`inject_input(...)` when another output sink is responsible for rendering.

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    engine, creature = await Terrarium.with_creature(
        "@kt-biome/creatures/general"
    )
    try:
        await creature.inject_input(
            "Explain the difference between a creature and a terrarium."
        )
    finally:
        await engine.shutdown()


asyncio.run(main())
```

The creature uses its configured output module. For simple streaming text in your
own code, prefer `Creature.chat(...)`.

## Step 4 — Capture output with lower-level `Agent`

Goal: route output into your own handler instead of stdout. This is an advanced
shape for custom transports; most applications should start with `Creature.chat()`
or `Studio.sessions.chat`.

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    parts: list[str] = []

    agent = Agent.from_path("@kt-biome/creatures/general")
    agent.set_output_handler(
        lambda text: parts.append(text),
        replace_default=True,
    )

    await agent.start()
    try:
        await agent.inject_input(
            "Describe three practical uses of a terrarium."
        )
    finally:
        await agent.stop()

    print("".join(parts))


asyncio.run(main())
```

`replace_default=True` disables stdout so your handler is the only sink.

## Step 5 — Embed a whole terrarium

Goal: drive a multi-agent setup from Python instead of the CLI.

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    async with await Terrarium.from_recipe(
        "@kt-biome/terrariums/swe_team"
    ) as engine:
        swe = engine["swe"]
        async for chunk in swe.chat("Summarize the team topology."):
            print(chunk, end="", flush=True)
        print()


asyncio.run(main())
```

For programmatic *control* of a running terrarium (add creatures,
connect channels, observe events), use methods on `Terrarium` itself:
`add_creature`, `connect`, `disconnect`, `subscribe`, and `shutdown`.
For user-facing management concerns above the engine, use `Studio`.

## Step 6 — Manage sessions with Studio

Goal: use the same management facade as the CLI and dashboard: active
sessions, saved-session persistence, catalog, settings, attach policies,
and editor workflows.

```python
import asyncio

from kohakuterrarium import Studio


async def main() -> None:
    async with Studio() as studio:
        session = await studio.sessions.start_creature(
            "@kt-biome/creatures/general"
        )
        cid = session.creatures[0]["creature_id"]

        stream = await studio.sessions.chat.chat(
            session.session_id,
            cid,
            "What does Studio manage?",
        )
        async for chunk in stream:
            print(chunk, end="", flush=True)
        print()


asyncio.run(main())
```

`Studio` wraps a `Terrarium` engine and adds management namespaces:
`catalog`, `identity`, `sessions`, `persistence`, `attach`, and
`editors`.

## Step 7 — Compose agents as values

The real leverage of "agents are Python objects" is that you can put
one inside anything else: inside a plugin, inside a trigger, inside a
tool, inside another agent's output module. The
[composition algebra](../concepts/python-native/composition-algebra.md)
gives you operators (`>>`, `|`, `&`, `*`) for the common shapes —
sequence, fallback, parallel, retry. When a pipeline of plain functions
starts to feel natural, reach for those.

## What you learned

- A `Creature` is a regular Python object hosted by `Terrarium`; `chat()` turns it into an async iterator.
- Lower-level `Agent` is available when you need `set_output_handler` or direct event control.
- `Terrarium` runs one or many creatures in graph topology.
- `Studio` manages active sessions, saved sessions, catalog, identity,
  attach policy, and editor workflows above the engine.
- The CLI is one consumer of these objects; your application can be
  another.

## What to read next

- [Agent as a Python object](../concepts/python-native/agent-as-python-object.md)
  — the concept, with patterns this unlocks.
- [Programmatic usage guide](../guides/programmatic-usage.md) — the
  task-oriented reference for the Python surface.
- [Composition algebra](../concepts/python-native/composition-algebra.md)
  — operators for wiring agents into Python pipelines.
- [Python API reference](../reference/python.md) — exact signatures.
