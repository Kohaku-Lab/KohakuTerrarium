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

**End state:** a minimal script that starts a creature, injects an
input, captures output through a custom handler, and shuts down cleanly.
Then the same thing using `Creature.chat()` for event streaming. Then a
terrarium and Studio session, embedded the same way.

**Prerequisites:** [First Creature](first-creature.md). You need the
package installed in a mode where you can `import kohakuterrarium`.

An agent in this framework is not a config — it is a Python object. A
config describes one; `Agent.from_path(...)` builds one; you own the
object. Sub-agents, `Terrarium` engines, `Creature` handles, and
`Studio` sessions are the same shape. See
[agent-as-python-object](../concepts/python-native/agent-as-python-object.md)
for the full mental model.

## Step 1 — Install editable

Goal: have `kohakuterrarium` importable from your venv.

From the repo root:

```bash
uv pip install -e .[dev]
```

The `[dev]` extras bring in the testing helpers you may want later.

## Step 2 — Minimal embed

Goal: build an agent, start it, feed it one input, stop it.

`demo.py`:

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    agent = Agent.from_path("@kt-biome/creatures/general")

    await agent.start()
    try:
        await agent.inject_input(
            "In one sentence, what is a creature in KohakuTerrarium?"
        )
    finally:
        await agent.stop()


asyncio.run(main())
```

Run it:

```bash
python demo.py
```

The default stdout output module prints the response. Three things to
notice:

1. `Agent.from_path` resolves `@kt-biome/...` the same way the CLI
   does.
2. `start()` initialises controller + tools + triggers + plugins.
3. `inject_input(...)` is the programmatic equivalent of a user typing
   a message on the CLI input module.

## Step 3 — Capture output yourself

Goal: route output into your own code instead of stdout.

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
            "Explain the difference between a creature and a terrarium."
        )
    finally:
        await agent.stop()

    print("".join(parts))


asyncio.run(main())
```

`replace_default=True` disables stdout so your handler is the only sink.
This is the right shape for a web backend, a bot, or anything that
wants to own rendering.

## Step 4 — Use `Creature.chat()` for streaming

Goal: get an async iterator of chunks, not a push handler. Useful when
you want an `async for` loop over the response.

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    engine, creature = await Terrarium.with_creature(
        "@kt-biome/creatures/general"
    )

    try:
        async for chunk in creature.chat(
            "Describe three practical uses of a terrarium."
        ):
            print(chunk, end="", flush=True)
        print()
    finally:
        await engine.shutdown()


asyncio.run(main())
```

`Creature` is the engine-level wrapper around the same underlying
`Agent`. It adds graph membership and gives you an `AsyncIterator[str]`
per `chat(...)` call.

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

- An `Agent` is a regular Python object — build, start, inject, stop.
- `set_output_handler` swaps the output sink. `Creature.chat()` turns
  an engine-hosted creature into an async iterator.
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
