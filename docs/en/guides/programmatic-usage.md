---
title: Programmatic usage
summary: Drive Studio, Terrarium, Creature, and Agent from your own Python code.
tags:
  - guides
  - python
  - embedding
---

# Programmatic Usage

For readers embedding agents inside their own Python code.

A creature isn't a config file — the config describes one. A running creature is an async Python object hosted by a `Terrarium` engine. `Studio` is the management facade above that engine: catalog, identity, active sessions, saved sessions, attach policies, and editor workflows. Everything in KohakuTerrarium is callable and awaitable. Your code is the orchestrator; agents are workers you invoke.

Concept primer: [Studio](../concepts/studio.md), [Terrarium](../concepts/multi-agent/terrarium.md), [agent as a Python object](../concepts/python-native/agent-as-python-object.md), [composition algebra](../concepts/python-native/composition-algebra.md).

## Entry points

| Surface | Use when |
|---|---|
| `Studio` | The management facade. Use it for packages/catalog, settings/identity, active sessions, saved sessions, attach policy, and editor workflows. |
| `Terrarium` | The runtime engine. Add creatures, connect them, observe events. Same engine handles solo and multi-creature workloads. |
| `Creature` | A running creature in the engine — `chat()`, `inject_input()`, `get_status()`. Returned by `Terrarium.add_creature` / `with_creature`. |
| `Agent` | Lower-level: the LLM controller behind a creature. Use when you need direct control over events, triggers, or output handlers. |

The top-level imports are stable: `from kohakuterrarium import Studio, Terrarium, Creature, EngineEvent, EventFilter`.

For multi-agent Python pipelines without an engine, see [Composition](composition.md).

## `Studio` — management facade

Use `Studio` when you are embedding the same responsibilities as the
CLI or dashboard: start/list/stop sessions, resume saved `.kohakutr`
files, inspect packages, edit workspaces, or expose attach streams.

```python
import asyncio
from kohakuterrarium import Studio

async def main():
    async with Studio() as studio:
        session = await studio.sessions.start_creature(
            "@kt-biome/creatures/general"
        )
        cid = session.creatures[0]["creature_id"]

        stream = await studio.sessions.chat.chat(
            session.session_id,
            cid,
            "Explain this project in one paragraph.",
        )
        async for chunk in stream:
            print(chunk, end="", flush=True)

asyncio.run(main())
```

Common construction helpers:

- `Studio()` — create an empty Studio with an empty engine.
- `await Studio.with_creature(config, *, pwd=None)` — create Studio + a one-creature session.
- `await Studio.from_recipe(recipe, *, pwd=None)` — create Studio + a recipe session.
- `await Studio.resume(store, *, pwd=None, llm_override=None)` — resume a saved session into a new Studio.
- `await studio.shutdown()` — stop the underlying engine; also called by `async with`.

Important namespaces:

- `studio.sessions` — active graph/session lifecycle and per-creature chat/control/state helpers.
- `studio.catalog` — packages, built-ins, workspace creature/module listing, introspection.
- `studio.identity` — LLM profiles/backends, API keys, Codex auth, MCP, UI preferences.
- `studio.persistence` — saved-session list/resolve/resume/fork/history/viewer/export/delete.
- `studio.attach` — available live attach policies for chat, channel observer, trace/logs, files, pty.
- `studio.editors` — workspace creature/module scaffolding and writes.

See [Studio guide](studio.md) for task-oriented examples of each namespace.

## `Terrarium` — the engine

One engine per process hosts every creature. A solo agent is a 1-creature graph; a recipe is a connected graph with channels.

### Solo creature

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    engine, alice = await Terrarium.with_creature("@kt-biome/creatures/swe")
    try:
        async for chunk in alice.chat("Explain what this codebase does."):
            print(chunk, end="", flush=True)
    finally:
        await engine.shutdown()

asyncio.run(main())
```

`Terrarium.with_creature(config)` constructs the engine and adds one creature in a 1-creature graph. The returned `Creature` exposes `chat()`, `inject_input()`, `is_running`, `graph_id`, and `get_status()`.

### Recipe (multi-creature)

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    engine = await Terrarium.from_recipe("@kt-biome/terrariums/swe_team")
    try:
        # creatures are running; talk to one of them by id
        swe = engine["swe"]
        async for chunk in swe.chat("Fix the off-by-one in pagination.py"):
            print(chunk, end="", flush=True)
    finally:
        await engine.shutdown()

asyncio.run(main())
```

A recipe describes "add these creatures, declare these channels, wire these listen/send edges". `from_recipe()` walks it, lands every creature in one graph, and starts them.

### Async context manager

```python
async with Terrarium() as engine:
    alice = await engine.add_creature("@kt-biome/creatures/general")
    bob   = await engine.add_creature("@kt-biome/creatures/general")
    await engine.connect(alice, bob, channel="alice_to_bob")
    # ...
# shutdown() runs on exit
```

### Hot-plug

Topology can change at runtime. Cross-graph `connect()` merges two graphs (environments union, attached session stores merge into one). `disconnect()` may split a graph (the parent session is copied to each side).

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")
    # a and b live in separate graphs

    result = await engine.connect(a, b, channel="a_to_b")
    # result.delta_kind == "merge" — a and b now share one graph,
    # one environment, one session store

    await engine.disconnect(a, b, channel="a_to_b")
    # split back into two graphs; each carries the merged history
```

See [`examples/code/terrarium_hotplug.py`](../../examples/code/terrarium_hotplug.py).

### Observing engine events

```python
from kohakuterrarium import EventFilter, EventKind

async with Terrarium() as engine:
    async def watch():
        async for ev in engine.subscribe(
            EventFilter(kinds={EventKind.TOPOLOGY_CHANGED, EventKind.CREATURE_STARTED})
        ):
            print(ev.kind.value, ev.creature_id, ev.payload)
    asyncio.create_task(watch())
    # ... drive the engine
```

Every observable thing the engine does — text chunks, channel messages, topology changes, session forks, errors — surfaces as an `EngineEvent`. `EventFilter` AND-combines kinds, creature IDs, graph IDs, and channel names.

### Key methods

- `await Terrarium.with_creature(config)` — engine + one creature.
- `await Terrarium.from_recipe(recipe)` — engine + a recipe applied.
- `await Terrarium.resume(store, *, pwd=None, llm_override=None)` — build an engine and adopt a saved session.
- `await engine.adopt_session(store, *, pwd=None, llm_override=None)` — resume into an existing engine and return the graph id.
- `await engine.add_creature(config, *, graph=None, start=True)` — add to an existing graph or mint a new singleton graph.
- `await engine.remove_creature(creature)` — stop and remove; may split the graph.
- `await engine.add_channel(graph, name, kind=...)` — declare a channel.
- `await engine.connect(a, b, channel=...)` — wire `a → b`; merges graphs if needed.
- `await engine.disconnect(a, b, channel=...)` — drop one or all edges; may split.
- `await engine.wire_output(creature, sink)` / `await engine.unwire_output(creature, sink_id)` — secondary output sinks.
- `engine[id]`, `id in engine`, `for c in engine`, `len(engine)` — pythonic accessors.
- `engine.list_graphs()` / `engine.get_graph(graph_id)` — graph introspection.
- `engine.status()` / `engine.status(creature)` — roll-up or per-creature status dict.
- `await engine.shutdown()` — stop every creature; idempotent.

Use `Terrarium` for runtime mechanics. Use `Studio` when you also need catalog, settings, saved-session, attach, or editor management.

## `Agent` — full control

```python
import asyncio
from kohakuterrarium.core.agent import Agent

async def main():
    agent = Agent.from_path("@kt-biome/creatures/swe")
    agent.set_output_handler(
        lambda text: print(text, end=""),
        replace_default=True,
    )
    await agent.start()
    await agent.inject_input("Explain what this codebase does.")
    await agent.stop()

asyncio.run(main())
```

Key methods:

- `Agent.from_path(path, *, input_module=..., output_module=..., session=..., environment=..., llm_override=..., pwd=...)` — build from a config folder or `@pkg/...` ref.
- `await agent.start()` / `await agent.stop()` — lifecycle.
- `await agent.run()` — the built-in loop (pulls from input, dispatches triggers, runs controller).
- `await agent.inject_input(content, source="programmatic")` — push input bypassing the input module.
- `await agent.inject_event(TriggerEvent(...))` — push any event.
- `agent.interrupt()` — stop the current processing cycle (non-blocking).
- `agent.switch_model(profile_name)` — change LLM at runtime.
- `agent.llm_identifier()` — read the canonical `provider/name[@variations]` identifier.
- `agent.set_output_handler(fn, replace_default=False)` — add or replace an output sink.
- `await agent.add_trigger(trigger)` / `await agent.remove_trigger(id)` — runtime trigger management.

Properties:

- `agent.is_running: bool`
- `agent.tools: list[str]`, `agent.subagents: list[str]`
- `agent.conversation_history: list[dict]`

## `Creature` — streaming chat

`Creature.chat(message)` yields text chunks as the controller streams.
Tool activity and sub-agent events are still emitted through the
underlying output/event paths; `Creature` focuses on the simple text
stream and status handle.

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    engine, creature = await Terrarium.with_creature("@kt-biome/creatures/swe")
    try:
        async for chunk in creature.chat("What does this do?"):
            print(chunk, end="")
        print()
    finally:
        await engine.shutdown()

asyncio.run(main())
```

Use `Creature.inject_input(message, source=...)` when you want to push
input without draining output, and `Creature.get_status()` when you need
model, tools, sub-agents, graph id, channels, and working-directory
status.

## Output handling

`set_output_handler` lets you hook any callable:

```python
def handle(text: str) -> None:
    my_logger.info(text)

agent.set_output_handler(handle, replace_default=True)
```

For multiple sinks (TTS, Discord, file), configure `named_outputs` in the YAML and the agent routes automatically.

## Event-level control

```python
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event

await agent.inject_event(create_user_input_event("Hi", source="slack"))
await agent.inject_event(TriggerEvent(
    type="context_update",
    content="User just navigated to page /settings.",
    context={"source": "frontend"},
))
```

`type` can be any string the controller is wired to handle — `user_input`, `idle`, `timer`, `channel_message`, `context_update`, `monitor`, or your own. See [reference/python](../reference/python.md).

## Multi-tenant servers

The HTTP API uses `Studio` as its management facade over a shared `Terrarium` engine. API routes start sessions, chat with creatures, inspect settings, and resume saved sessions through Studio namespaces rather than duplicating those policies. If you are building your own server, follow the same shape:

```python
from kohakuterrarium import Studio

studio = Studio()
session = await studio.sessions.start_creature(
    "@kt-biome/creatures/swe",
    pwd="/srv/workspaces/project-a",
)
cid = session.creatures[0]["creature_id"]

stream = await studio.sessions.chat.chat(session.session_id, cid, "Hi")
async for chunk in stream:
    print(chunk, end="")

print(studio.engine.status(cid))
await studio.sessions.stop(session.session_id)
```

For the FastAPI handlers themselves, dependency helpers provide the per-process `Studio` / `Terrarium` objects. Route handlers should delegate to Studio namespaces for catalog, identity, active-session, persistence, attach, and editor policy.

## Stopping cleanly

Always pair `start()` with `stop()`:

```python
agent = Agent.from_path("...")
try:
    await agent.start()
    await agent.inject_input("...")
finally:
    await agent.stop()
```

Or use `Terrarium`, `Studio`, or `compose.agent()` as async context managers where appropriate.

Interrupts are safe from any asyncio task:

```python
agent.interrupt()           # non-blocking
```

The controller checks its interrupt flag between LLM streaming steps.

## Custom session / environment

```python
from kohakuterrarium.core.session import Session
from kohakuterrarium.core.environment import Environment

env = Environment(env_id="my-app")
session = env.get_session("my-agent")
session.extra["db"] = my_db_connection

agent = Agent.from_path("...", session=session, environment=env)
```

Anything you put in `session.extra` is accessible to tools via `ToolContext.session`.

## Attaching session persistence

```python
from kohakuterrarium.session.store import SessionStore

store = SessionStore("/tmp/my-session.kohakutr")
store.init_meta(
    session_id="s1",
    config_type="agent",
    config_path="path/to/creature",
    pwd="/tmp",
    agents=["my-agent"],
)
agent.attach_session_store(store)
```

For simple cases `Terrarium(session_dir=...)` handles this automatically — pass `session_dir=` to the engine and it attaches a per-graph store on `attach_session`.

If your agent generates binary artifacts (for example provider-native images),
attach the session store before the run so those artifacts can be persisted
beside the session file under `<session>.artifacts/`.

## Testing

```python
from kohakuterrarium.testing.agent import TestAgentBuilder

env = (
    TestAgentBuilder()
    .with_llm_script([
        "Let me check. [/bash]@@command=ls\n[bash/]",
        "Done.",
    ])
    .with_builtin_tools(["bash"])
    .with_system_prompt("You are helpful.")
    .build()
)

await env.inject("List files.")
assert "Done" in env.output.all_text
assert env.llm.call_count == 2
```

`ScriptedLLM` is deterministic; `OutputRecorder` captures chunks/writes/activities for assertions.

## Troubleshooting

- **`await agent.run()` never returns.** `run()` is the full event loop; it exits when the input module closes (e.g. CLI gets EOF) or when a termination condition fires. Use `inject_input` + `stop` instead for one-shot interactions.
- **Output handler not called.** Confirm `replace_default=True` if you don't want stdout as well; make sure the agent started before injecting.
- **Hot-plugged creature never sees messages.** Use `engine.connect(sender, receiver, channel=...)` — the engine handles channel registration and trigger injection. Adding a creature with `add_creature` alone gives it a singleton graph with no inbound channels.
- **`Creature.chat` appears to hang.** Another caller may be using the same creature; serialize access per creature, or start separate sessions/creatures per independent caller.

## See also

- [Composition](composition.md) — Python-side multi-agent pipelines.
- [Custom Modules](custom-modules.md) — write the tools/inputs/outputs you wire in.
- [Reference / Python API](../reference/python.md) — exhaustive signatures.
- [examples/code/](../../examples/code/) — runnable scripts for each pattern.
