# First Python Embedding

**Problem:** you want to run a creature from inside your own Python
application — capture its output, drive its input from code, compose it
with other code.

**End state:** a minimal script that starts a creature, injects an
input, captures output through a custom handler, and shuts down cleanly.
Then the same thing using `AgentSession` for event streaming. Then a
terrarium, embedded the same way.

**Prerequisites:** [First Creature](first-creature.md). You need the
package installed in a mode where you can `import kohakuterrarium`.

An agent in this framework is not a config — it is a Python object. A
config describes one; `Agent.from_path(...)` builds one; you own the
object. Sub-agents, terrariums, and sessions are the same shape. See
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
    agent = Agent.from_path("@kt-defaults/creatures/general")

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

1. `Agent.from_path` resolves `@kt-defaults/...` the same way the CLI
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

    agent = Agent.from_path("@kt-defaults/creatures/general")
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

## Step 4 — Use `AgentSession` for streaming

Goal: get an async iterator of chunks, not a push handler. Useful when
you want an `async for` loop over the response.

```python
import asyncio

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.serving.agent_session import AgentSession


async def main() -> None:
    agent = Agent.from_path("@kt-defaults/creatures/general")
    session = AgentSession(agent)

    await session.start()
    try:
        async for chunk in session.chat(
            "Describe three practical uses of a terrarium."
        ):
            print(chunk, end="", flush=True)
        print()
    finally:
        await session.stop()


asyncio.run(main())
```

`AgentSession` is the transport-friendly wrapper used by the HTTP and
WebSocket layers. Same agent underneath; it just gives you an
`AsyncIterator[str]` per `chat(...)` call.

## Step 5 — Embed a whole terrarium

Goal: drive a multi-agent setup from Python instead of the CLI.

```python
import asyncio

from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime


async def main() -> None:
    config = load_terrarium_config("@kt-defaults/terrariums/swe_team")
    runtime = TerrariumRuntime(config)

    await runtime.start()
    try:
        # runtime.run() drives the main loop until a stop signal.
        # For a script, you can interact through runtime's API or
        # just let the creatures run to quiescence.
        await runtime.run()
    finally:
        await runtime.stop()


asyncio.run(main())
```

For programmatic *control* of a running terrarium (send on a channel,
start a creature, observe messages), use `TerrariumAPI`
(`kohakuterrarium.terrarium.api`). That is the same facade the
terrarium-management tools route through.

## Step 6 — Compose agents as values

The real leverage of "agents are Python objects" is that you can put
one inside anything else: inside a plugin, inside a trigger, inside a
tool, inside another agent's output module. The
[composition algebra](../concepts/python-native/composition-algebra.md)
gives you operators (`>>`, `|`, `&`, `*`) for the common shapes —
sequence, fallback, parallel, retry. When a pipeline of plain functions
starts to feel natural, reach for those.

## What you learned

- An `Agent` is a regular Python object — build, start, inject, stop.
- `set_output_handler` swaps the output sink. `AgentSession.chat()`
  turns it into an async iterator.
- `TerrariumRuntime` runs a whole multi-agent config with the same
  shape.
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
