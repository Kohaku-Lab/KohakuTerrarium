# Agent as a Python object

## What it is

In KohakuTerrarium, an agent is not a config file — the config file
just describes one. A running agent is a `kohakuterrarium.core.agent.Agent`
instance: an async Python object you construct, start, feed events,
and stop. A sub-agent is the same object, nested. A terrarium is
another Python object that owns several of them.

Everything is callable, awaitable, composable.

## Why it matters

Most agent systems expose two layers:

1. A configuration layer (YAML, JSON) for "the agent."
2. A runtime (usually a server or CLI) that reads the config and
   produces behaviour.

Behaviour you want to build on top usually has to live in a third
layer — another process, another container, another plugin system.
That is a lot of hops to do something that could be a function call.

KohakuTerrarium collapses the layers: you can `import
kohakuterrarium`, load a config, spawn an agent, call it, and do
whatever you want with its events. An agent is a value. Values can
be put inside other values.

## What the key surface looks like

```python
from kohakuterrarium.core.agent import Agent

agent = Agent.from_path("@kt-defaults/creatures/swe")
agent.set_output_handler(lambda text: print(text, end=""), replace_default=True)

await agent.start()
await agent.inject_input("Explain what this codebase does.")
await agent.stop()
```

Or the transport-friendly wrapper:

```python
from kohakuterrarium.serving.agent_session import AgentSession

session = AgentSession(Agent.from_path("@kt-defaults/creatures/swe"))
await session.start()
async for event in session.send_input("What does this do?"):
    print(event)
await session.stop()
```

Terrariums follow the same shape:

```python
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.terrarium.config import load_terrarium_config

runtime = TerrariumRuntime(load_terrarium_config("@kt-defaults/terrariums/swe_team"))
await runtime.start()
await runtime.run()
await runtime.stop()
```

## What you can therefore do

The real payoff is not "agents are Python" — it is "because agents
are Python, and modules are Python, you can put an agent inside any
module." Some concrete patterns:

### Agent inside a plugin (smart guard)

A `pre_tool_execute` plugin whose implementation runs a small nested
agent to decide whether to allow the tool call. The outer creature
keeps its main conversation clean; the guard reasons in its own
context.

### Agent inside a plugin (seamless memory)

A `pre_llm_call` plugin runs a tiny retrieval agent that searches the
session's event log (or an external vector store), picks relevant
past content, and injects it into the LLM messages. From the outer
creature's point of view, its memory just "works better."

### Agent inside a trigger (adaptive watcher)

Instead of `timer: 60s`, a custom trigger whose `fire()` body runs a
small agent each tick. The agent looks at the current state and
decides whether to wake the outer creature. Ambient intelligence
that does not follow a fixed rule.

### Agent inside a tool (context-isolated specialist)

A tool that, when called, spawns a fresh agent to do the work. The
LLM calls the tool the same way it calls any tool, but the tool's
implementation is an entire sub-system. Useful when the sub-system
needs to be wholly isolated — different model, different tools,
different prompt.

### Agent inside an output module (routing receptionist)

An output module whose job is to decide *where* each chunk of text
goes. For simple rules this is a switch statement; for nuanced
routing, wire in an agent that reads the stream and decides.

## The cross-refs this enables

The [patterns](../patterns.md) doc spells each of these out with
minimal snippets. This concept doc exists to make clear that *none
of them are special*. They are straightforward applications of
"agent is a first-class Python value."

## Don't be bounded

You do not have to use Python to build creatures — configs alone are
enough for most cases. But if a creature config runs into a wall and
you find yourself wanting "an agent that judges, inside a step that
the agent is taking," the Python substrate is already there, no new
plugin system required.

## See also

- [Composition algebra](composition-algebra.md) — ergonomic operators for Python-side pipelines.
- [Patterns](../patterns.md) — surprising uses that this unlocks.
- [guides/programmatic-usage.md](../../guides/programmatic-usage.md) — the task-oriented version of this page.
- [reference/python.md](../../reference/python.md) — signatures and API index.
