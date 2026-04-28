---
title: Programmatic usage
summary: Drive Terrarium, Creature, Agent, and AgentSession from your own Python code.
tags:
  - guides
  - python
  - embedding
---

# Programmatic Usage

For readers embedding agents inside their own Python code.

A creature isn't a config file — the config describes one. A running creature is an async Python object hosted by a `Terrarium` engine. Everything in KohakuTerrarium is callable and awaitable. Your code is the orchestrator; agents are workers you invoke.

Concept primer: [agent as a Python object](../concepts/python-native/agent-as-python-object.md), [composition algebra](../concepts/python-native/composition-algebra.md).

## Entry points

| Surface | Use when |
|---|---|
| `Terrarium` | The single runtime engine. Add creatures, connect them, observe events. Same engine handles solo and multi-creature workloads. |
| `Creature` | A running creature in the engine — `chat()`, `inject_input()`, `get_status()`. Returned by `Terrarium.add_creature` / `with_creature`. |
| `Agent` | Lower-level: the LLM controller behind a creature. Use when you need direct control over events, triggers, or output handlers. |
| `AgentSession` | Legacy streaming wrapper. Functional, but `Creature.chat()` is the new equivalent. |

The top-level imports are stable: `from kohakuterrarium import Terrarium, Creature, EngineEvent, EventFilter`.

For multi-agent Python pipelines without an engine, see [Composition](composition.md).

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
- `await Terrarium.resume(store)` — *not yet implemented*.
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

The legacy `TerrariumRuntime` and `KohakuManager` still exist on disk and remain functional during the transition. New code should reach for `Terrarium`.

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

## `AgentSession` — streaming chat

```python
import asyncio
from kohakuterrarium.serving.agent_session import AgentSession

async def main():
    session = await AgentSession.from_path("@kt-biome/creatures/swe")
    await session.start()
    async for chunk in session.chat("What does this do?"):
        print(chunk, end="")
    print()
    await session.stop()

asyncio.run(main())
```

`chat(message)` yields text chunks as the controller streams. Tool activity and sub-agent events are surfaced through the output module's activity callbacks — `AgentSession` focuses on the text stream; for richer events, use `Agent` + a custom output module.

`AgentSession.get_status()` also exposes `llm_name`, the canonical provider-qualified model identifier used by the web UI and `/model`.

Builders: `AgentSession.from_path(...)`, `from_config(AgentConfig)`, `from_agent(pre_built_agent)`.

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

The HTTP API uses the `Terrarium` engine as a single shared singleton — every request adds, removes, or chats with creatures by ID through the engine's pythonic accessors:

```python
from kohakuterrarium import Terrarium

engine = Terrarium(session_dir="/var/kt/sessions")

alice = await engine.add_creature("@kt-biome/creatures/swe", creature_id="alice")
async for chunk in alice.chat("Hi"):
    print(chunk, end="")

print(engine.status("alice"))
await engine.stop("alice")
```

For the FastAPI handlers themselves, `kohakuterrarium.api.deps.get_engine()` returns the per-process singleton; the legacy `get_manager()` (`KohakuManager`) is still wired in until every route is cut over.

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

Or use `AgentSession` / `compose.agent()` which are async context managers.

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
- **`AgentSession.chat` hangs.** Another caller is using the agent; sessions serialize input. Use one `AgentSession` per caller.

## See also

- [Composition](composition.md) — Python-side multi-agent pipelines.
- [Custom Modules](custom-modules.md) — write the tools/inputs/outputs you wire in.
- [Reference / Python API](../reference/python.md) — exhaustive signatures.
- [examples/code/](../../examples/code/) — runnable scripts for each pattern.
