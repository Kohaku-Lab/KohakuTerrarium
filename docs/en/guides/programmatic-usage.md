---
title: Programmatic usage
summary: Drive Agent, Terrarium, and Creature from your own Python code, with typed turns, strict errors, and engine-owned sessions.
tags:
  - guides
  - python
  - embedding
---

# Programmatic Usage

For readers embedding agents inside their own Python code.

A creature isn't a config file; the config describes one. A running
agent is an async Python object, and the programmatic surface is built
around three promises:

1. **Typed turns.** `run()` returns a `TurnResult` (status, text, tool
   calls, usage, duration); `run_stream()` yields typed events live.
2. **Strict errors.** Programmatic constructors and turns **raise**
   typed `kt.errors.*` exceptions instead of degrading silently:
   a dead provider is an exception, not a clean empty reply.
3. **Engine-owned sessions.** Persistence is a keyword argument
   (`session=`, `Terrarium(session_dir=...)`), not a ceremony.

Exact signatures: [reference/python](../reference/python.md).

## Entry points

| Surface | Use when |
|---|---|
| `Agent` | One agent, no engine features. `await Agent.build(...)` then `run` / `run_stream`. |
| `Terrarium` | The runtime engine. Per-creature working dirs, session files, channels, hot-plug, events. Use it as soon as you run more than one agent, or one agent you want persisted. |
| `Creature` | A running agent inside the engine: `run`, `run_stream`, `attach`, `get_status`. Returned by `add_creature` / `with_creature`. |
| `Studio` | The management facade above the engine (catalog, saved sessions, editors). See [the Studio guide](studio.md). |
| `compose` | Request-scoped pipelines (`>>`, `&`, `\|`, `*`); see [Composition](composition.md). |

Top-level imports: `from kohakuterrarium import Agent, Terrarium,
Creature, TurnResult, TextChunk, Activity, TurnEnded, SessionReader,
tool, errors, validate`.

## One agent, one turn

```python
import asyncio
from kohakuterrarium import Agent, TextChunk, TurnEnded

async def main():
    agent = await Agent.build("@kt-biome/creatures/general")
    await agent.start()
    try:
        # Buffered: one TurnResult with status / text / usage.
        result = await agent.run("What is a terrarium?", timeout=300)
        print(result.text)
        if result.usage:
            print(f"[{result.usage.get('total_tokens', '?')} tokens]")

        # Streamed: typed events as they happen.
        async for event in agent.run_stream("How would you build one?"):
            if isinstance(event, TextChunk):
                print(event.text, end="", flush=True)
            elif isinstance(event, TurnEnded):
                print(f"\n[turn status: {event.result.status}]")
    finally:
        await agent.stop()

asyncio.run(main())
```

(Full script: [`examples/code/programmatic_chat.py`](../../../examples/code/programmatic_chat.py).)

`Agent.build` accepts a config folder path, an `@pkg/...` package
reference, or an already-loaded `AgentConfig`. It returns an agent that
is **not started**, so always pair `await agent.start()` with
`await agent.stop()` (there is no `async with` on `Agent`).

`agent.run_forever()` is the legacy autonomous main loop (input module
+ triggers drive the agent until the input exits); it's what `kt run`
uses. Scripts almost always want `run` / `run_stream` instead.

## What raises, and when

The programmatic surface is strict by default:

- **Construction** (`Agent.build`, `engine.add_creature`) raises
  `kt.errors.ConfigNotFoundError` for a missing config or uninstalled
  package, `LLMNotConfiguredError` for an unresolvable model, and
  errors for unknown tools / broken plugins. Interactive frontends pass
  `strict=False` to degrade instead.
- **Turns** raise `TurnError` on failure and `TurnTimeoutError` on
  timeout. `timeout=` genuinely **interrupts** the turn (it does not
  abandon a still-burning LLM call). Pass `raise_on_error=False` to
  always get the `TurnResult` back and branch on `result.status`
  (`"ok"` / `"error"` / `"timeout"` / `"interrupted"`) yourself;
  that is the right shape for batch jobs.
- `run_stream` never raises mid-iteration: errors arrive as
  `Activity(kind="processing_error")` events and in the final
  `TurnEnded(result)`.

```python
from kohakuterrarium import errors

try:
    result = await agent.run("Grade this submission.", timeout=1800)
except errors.TurnTimeoutError:
    print("over budget; turn was interrupted")
except errors.TurnError as e:
    print(f"turn failed: {e}")
```

Validate a setup before a long run with
[`kt.validate`](../reference/python.md#validate): `validate.config`,
`validate.llm`, `validate.creature` (full dry-run build), and
`await validate.ping` (one real round-trip). `kt doctor` is the CLI
equivalent.

## Bring your own tools, plugins, and LLM

`@kt.tool` turns a plain function into an agent tool: schema from the
type hints, description from the docstring. Sync functions run in a
thread; async functions are awaited.

```python
import kohakuterrarium as kt

@kt.tool
def check_stock(item: str) -> str:
    """Look up how many units of an item are in stock."""
    return lookup(item)

agent = await kt.Agent.build(
    "@kt-biome/creatures/general",
    llm="default",                 # profile name; a typo raises here
    tools=[check_stock],           # instances, in the initial prompt
    plugins=[MyTracePlugin()],
)
```

After construction you can extend a live agent; each call refreshes
the system prompt so the controller actually sees the change:

```python
agent.add_tool(other_tool)
await agent.add_plugin(plugin)     # on_load fires even post-start
agent.add_subagent(subagent_cfg)
```

`llm=` accepts four shapes everywhere (`Agent.build`,
`engine.add_creature`, `compose.agent`):

- `None`: resolve from the config;
- a selector string: profile / preset name or
  `provider/model[@variations]`;
- an `LLMProfile` instance;
- a provider instance, e.g. `ScriptedLLM` for tests.

`io=` selects how much of the config's I/O boots: `"config"` (as
declared), `"none"` (input suppressed), or `"headless"` (input
suppressed AND default output silenced; the batch default, so N
concurrent agents don't interleave on your console).

## The engine: `Terrarium`

One engine per process hosts every creature; a solo agent is a
1-creature graph. Reach for the engine when you want per-creature
working directories, session files, channels, or runtime topology.

### The canonical batch pattern

One shared engine, one creature per work folder, each with its own
`pwd` and session file
([`examples/code/batch_grading.py`](../../../examples/code/batch_grading.py)):

```python
import asyncio
from kohakuterrarium import Terrarium

async def grade_one(engine, folder, gate):
    async with gate:
        creature = await engine.add_creature(
            "@kt-biome/creatures/general",
            llm="default",
            pwd=folder,                                   # no global os.chdir
            session=folder / "scoring_session.kohakutr",  # resumable later
        )
        try:
            return folder.name, await creature.run(
                PROMPT, timeout=1800, raise_on_error=False
            )
        finally:
            await engine.remove_creature(creature)

async def main():
    gate = asyncio.Semaphore(8)
    async with Terrarium() as engine:
        results = await asyncio.gather(*(grade_one(engine, d, gate) for d in folders))
    for name, r in results:
        print(name, r.status, r.duration_s, (r.usage or {}).get("total_tokens"))
```

### Recipes

```python
from kohakuterrarium import Terrarium

async with await Terrarium.from_recipe("@kt-biome/terrariums/swe_team") as engine:
    swe = engine["swe"]
    result = await swe.run("Fix the off-by-one in pagination.py")
    print(result.text)
```

A recipe describes "add these creatures, declare these channels, wire
these listen/send edges". `from_recipe` lands every creature in one
graph and starts them. Add `session=` to `apply_recipe` (or build the
engine with `session_dir=`) to persist the whole graph.

### Hot-plug and topology

Topology changes at runtime. Cross-graph `connect()` auto-merges two
graphs (environments union, session stores merge); `disconnect()` /
`remove_creature()` may auto-split. All graph channels are broadcast:
every listener receives every send.

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")

    result = await engine.connect(a, b, channel="a_to_b")
    # result.delta_kind == "merge": one graph, one environment

    d = await engine.disconnect(a, b, channel="a_to_b")
    # d.delta_kind == "split": two graphs again, history copied to each
```

(Full script: [`examples/code/terrarium_hotplug.py`](../../../examples/code/terrarium_hotplug.py).)

The engine exposes public accessors for a graph's live state, with no
private-dict poking:

```python
from kohakuterrarium.core.channel import ChannelMessage

graph_id = engine.list_graphs()[0].graph_id
env = engine.environment(graph_id)          # live Environment
tasks = engine.channel(graph_id, "tasks")   # live broadcast channel or None
if tasks is not None:
    await tasks.send(ChannelMessage(sender="user", content="Fix the bug"))
```

### Observing engine events

The engine bus carries **structure** events (creatures added / started
/ stopped, topology changes, channel messages, wiring); per-creature
text and tool activity flow through the turn surface instead
(`run_stream` / `attach`).

```python
from kohakuterrarium import EventFilter, EventKind

async def watch(engine):
    async for ev in engine.subscribe(
        EventFilter(kinds={EventKind.TOPOLOGY_CHANGED, EventKind.CREATURE_STARTED})
    ):
        print(ev.kind.value, ev.creature_id, ev.payload)
```

The subscriber registers at the `subscribe()` call itself, so events
emitted before the first `await` are buffered; the
subscribe-then-trigger pattern can't lose its first event.
`engine.shutdown()` terminates live subscribers.

## `Creature`: the running handle

`Creature` mirrors the agent's turn surface and adds engine context:

- `await creature.run(content, timeout=..., raise_on_error=...)` → `TurnResult`
- `creature.run_stream(content)` → typed events
- `creature.attach()`: **non-destructive observer**: an async context
  manager streaming every typed event the creature emits, including
  out-of-band turns (triggers, channel messages). Multi-consumer; the
  default output and session store keep receiving everything.
- `await creature.chat(message)`: text-only sugar; prefer the typed
  drivers in new code.
- `creature.status`: `"not_started"` / `"idle"` / `"busy"` /
  `"stopped"` / `"error"`; `creature.get_status()` returns the full dict.

```python
async with creature.attach() as stream:
    async for ev in stream:
        log(ev)          # tool starts, text, errors: everything
```

## Sessions from code

Persistence is engine-owned (the old `SessionStore` + `init_meta` +
`attach_session` ceremony is gone):

```python
# Autosession: every graph gets runs/<graph_id>.kohakutr automatically.
engine = Terrarium(session_dir="runs/")

# Or per creature: exact file, True (default dir), False (off), or a store.
c = await engine.add_creature("@kt-biome/creatures/general",
                              session="runs/student-42.kohakutr")

# Resume later: fresh engine or into a running one.
engine2 = await Terrarium.resume("runs/student-42.kohakutr")
graph_id = await engine.adopt_session("runs/other.kohakutr")
```

`engine.shutdown()` closes every store it minted. Read a finished file
with `SessionReader` (meta, events, reassembled turns, search); see
[Sessions](sessions.md).

## Testing your integration

Inject a `ScriptedLLM` directly, with no monkeypatching:

```python
import kohakuterrarium as kt
from kohakuterrarium.testing.llm import ScriptedLLM

agent = await kt.Agent.build(cfg, llm=ScriptedLLM(["Hello!"]), io="headless")
await agent.start()
result = await agent.run("hi")
assert result.text == "Hello!"
assert agent.llm.call_count == 1
await agent.stop()
```

`engine.add_creature(path, llm=ScriptedLLM([...]))` works the same way.

## Stopping cleanly

- `Agent`: pair `start()` / `stop()` in `try/finally`.
- `Terrarium`: use `async with`; `shutdown()` runs on exit, stops every
  creature, and closes every session store the engine minted.
- `agent.interrupt()` / `creature.agent.interrupt()` cancels the active
  turn from any asyncio task (non-blocking).

## Troubleshooting

- **`await agent.run_forever()` never returns.** It's the autonomous
  main loop; it exits when the input module closes or a termination
  condition fires. Use `run` / `run_stream` for one-shot interactions.
- **`TurnError: turn failed` on the first call.** The provider call
  failed. Check `kt.validate.llm("<selector>")` and
  `await kt.validate.ping(...)` before blaming your code.
- **A hot-plugged creature never sees messages.** Use
  `engine.connect(sender, receiver, channel=...)`; `add_creature`
  alone gives it a singleton graph with no inbound channels.
- **Two `run()` calls on the same agent at once.** Turns serialize on
  the agent's processing lock; the second `run` waits for the first.
  For parallelism, use multiple creatures (the batch pattern).
- **Console noise from N concurrent agents.** Pass `io="headless"` so
  the config's default stdout output is silenced; consume text via
  `run` / `run_stream` / the session store.

## See also

- [Composition](composition.md): request-scoped pipelines.
- [Sessions](sessions.md): persistence, resume, `SessionReader`.
- [Packages](packages.md): `@pkg/...` refs and `packages.ensure`.
- [Reference / Python API](../reference/python.md): exact signatures.
- [`examples/code/`](../../../examples/code/): runnable scripts for
  each pattern (`batch_grading.py` is the canonical batch job).
