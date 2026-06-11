---
title: Embedding in Python
summary: Run an agent inside your own Python code. Typed turns, custom tools, engine-hosted creatures, session files, and resume.
tags:
  - tutorials
  - python
  - embedding
---

# First Python Embedding

**Problem:** you want to run a creature from inside your own Python
application: send it work, observe what it does, keep a record, resume
it later.

**End state:** a minimal script that builds an agent with
`Agent.build`, drives typed turns with `run` / `run_stream`, injects a
custom tool with `@kt.tool`, hosts creatures in a `Terrarium` with a
session file, reads the session back with `SessionReader`, and resumes
it.

**Prerequisites:** [First Creature](first-creature.md). You need the
package installed in a mode where you can `import kohakuterrarium`.

An agent in this framework is not a config; it is a Python object. A
config describes one; `Agent.build(...)` constructs one you own. See
[agent-as-python-object](../concepts/python-native/agent-as-python-object.md)
for the mental model.

## Step 1: Install editable

Goal: have `kohakuterrarium` importable from your venv.

From the repo root:

```bash
uv pip install -e .[dev]
```

The `[dev]` extras bring in the testing helpers you may want later.

## Step 2: One agent, one turn

Goal: build an agent, drive one turn, get a typed result.

`demo.py`:

```python
import asyncio

from kohakuterrarium import Agent


async def main() -> None:
    agent = await Agent.build("@kt-biome/creatures/general")
    await agent.start()
    try:
        result = await agent.run(
            "In one sentence, what is a creature in KohakuTerrarium?",
            timeout=300,
        )
        print(result.text)
        print(f"[status={result.status} {result.duration_s:.1f}s]")
    finally:
        await agent.stop()


asyncio.run(main())
```

Run it:

```bash
python demo.py
```

Three things to notice:

1. `Agent.build` resolves `@kt-biome/...` the same way the CLI does,
   and **raises** (`kt.errors.ConfigNotFoundError`,
   `LLMNotConfiguredError`, ...) if the setup is broken, instead of
   running and producing nothing.
2. `run()` returns a `TurnResult`: `status` (`"ok"` / `"error"` /
   `"timeout"` / `"interrupted"`), `text`, `error`, `tool_calls`,
   `usage`, `duration_s`. A failed turn raises `kt.errors.TurnError`
   by default; pass `raise_on_error=False` to branch on
   `result.status` yourself.
3. `timeout=` actually interrupts the turn; no tokens keep burning
   after a "timeout".

## Step 3: Stream the turn

Goal: render text as it arrives and see tool activity live.

```python
import asyncio

from kohakuterrarium import Agent, Activity, TextChunk, TurnEnded


async def main() -> None:
    agent = await Agent.build("@kt-biome/creatures/general")
    await agent.start()
    try:
        async for event in agent.run_stream("Plan a tropical terrarium."):
            if isinstance(event, TextChunk):
                print(event.text, end="", flush=True)
            elif isinstance(event, Activity):
                print(f"\n[{event.kind}] {event.detail}")
            elif isinstance(event, TurnEnded):
                print(f"\n[done: {event.result.status}]")
    finally:
        await agent.stop()


asyncio.run(main())
```

`run_stream` yields a typed union (`TextChunk | Activity | TurnEnded`)
and never raises mid-stream: errors arrive as
`Activity(kind="processing_error")` and in the final result.

## Step 4: Give it a tool from a plain function

Goal: extend the agent with your own capability, with no config files.

```python
import asyncio

import kohakuterrarium as kt

INVENTORY = {"moss": 12, "fern": 3}


@kt.tool
def check_stock(item: str) -> str:
    """Look up how many units of an item are in stock."""
    count = INVENTORY.get(item.lower())
    return f"{item}: {count} in stock" if count is not None else f"{item}: not found"


async def main() -> None:
    agent = await kt.Agent.build(
        "@kt-biome/creatures/general",
        tools=[check_stock],
    )
    await agent.start()
    try:
        result = await agent.run("Do we have ferns in stock?")
        print(result.text)
        print(f"[tools used: {[t.detail for t in result.tool_calls]}]")
    finally:
        await agent.stop()


asyncio.run(main())
```

`@kt.tool` derives the schema from the type hints and the description
from the docstring; sync functions run in a thread, async functions
are awaited. You can also add capabilities to a live agent
(`agent.add_tool(...)`, `await agent.add_plugin(...)`), and the system
prompt refreshes so the controller actually sees them.

## Step 5: Host it in the engine, with a session file

Goal: per-creature working directory + a resumable session file, with
zero persistence ceremony.

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    async with Terrarium() as engine:
        clerk = await engine.add_creature(
            "@kt-biome/creatures/general",
            pwd="workdir",                        # the creature's cwd
            session="runs/clerk.kohakutr",        # minted + closed for you
        )
        result = await clerk.run("Summarize the files in this directory.")
        print(result.text)


asyncio.run(main())
```

The engine hosts any number of creatures (the
[batch pattern](../guides/programmatic-usage.md#the-canonical-batch-pattern)
in [`examples/code/batch_grading.py`](../../../examples/code/batch_grading.py)
runs one creature per submission folder on a semaphore). Leaving the
`async with` block stops every creature and closes every session store
the engine minted. `Terrarium(session_dir="runs/")` persists every
graph automatically instead.

## Step 6: Read the session back

Goal: inspect what happened, offline, without touching the file's
status.

```python
from kohakuterrarium import SessionReader

with SessionReader("runs/clerk.kohakutr") as r:
    print(r.meta["session_id"], r.meta["status"])
    for turn in r.turns():
        tools = [tc["name"] for tc in turn.tool_calls]
        print(f"- {turn.user_text[:40]!r} -> {turn.assistant_text[:60]!r} {tools}")
```

`SessionReader` is read-only (it opens via
`SessionStore.open_readonly`), so inspection never bumps
`last_active` or flips `status`.

## Step 7: Resume it

Goal: pick the conversation back up in a new process.

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    async with await Terrarium.resume("runs/clerk.kohakutr") as engine:
        clerk = engine.list_creatures()[0]
        result = await clerk.run("Continue where you left off.")
        print(result.text)


asyncio.run(main())
```

`Terrarium.resume` rebuilds the topology from the config path recorded
in the session metadata and re-injects the saved conversation.
`engine.adopt_session(...)` does the same into an engine that is
already running other graphs.

## What you learned

- `Agent.build` is the canonical constructor; it raises typed
  `kt.errors.*` exceptions instead of degrading silently.
- `run()` returns a `TurnResult`; `run_stream()` yields typed events;
  `timeout=` interrupts for real.
- `@kt.tool` turns plain functions into agent tools; `tools=` /
  `add_tool` inject them.
- `Terrarium` hosts creatures with per-creature `pwd` and `session=`
  persistence; `SessionReader` reads the file back; `Terrarium.resume`
  continues it.

## What to read next

- [Programmatic usage guide](../guides/programmatic-usage.md): the
  task-oriented reference for the Python surface, including engine
  events, hot-plug, and validation.
- [Composition algebra](../guides/composition.md): `>>`, `&`, `|`,
  `*` operators for request-scoped pipelines.
- [Sessions guide](../guides/sessions.md): everything about
  `.kohakutr` files.
- [Python API reference](../reference/python.md): exact signatures.
