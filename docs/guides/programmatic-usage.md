# Programmatic Usage

Programmatic usage is one of the key features of KohakuTerrarium.

KohakuTerrarium is not only a config-driven runtime. It is also a Python framework for embedding creatures directly into your own applications, coordinating them in code, and mixing agent behavior with normal Python logic.

If the CLI is the easiest way to run a creature, the Python API is the strongest way to integrate creatures into your own product.

## Two paradigms

KohakuTerrarium supports two different ways to work.

### Config-driven runtime

You write a creature config folder, launch it with `kt run`, and the creature owns the interactive loop.

### Programmatic runtime

Your Python code is the orchestrator.
You create creatures, invoke them, stream or capture their output, coordinate them with code, and decide how long they live.

Use programmatic mode when:

- you are building a web server, Discord bot, backend worker, or desktop app
- the number and role of creatures are decided at runtime
- you want strict ordering or explicit control flow
- you want to mix agent calls with normal Python logic
- you want stronger orchestration than loose channel-based collaboration
- you want composition algebra as an application-owned orchestration layer

## Main Python surfaces

Most users will work with one of these layers:

| Surface | Use it when |
|---------|-------------|
| `AgentSession` | you want the simplest streaming chat interface for one creature |
| `Agent` | you want direct control over one creature runtime |
| `TerrariumRuntime` | you want to run a terrarium directly in code |
| `KohakuManager` | you want a service-style manager above creatures and terrariums |
| composition algebra | you want to treat creatures as composable programmatic steps |

## 1. The simplest path: `AgentSession`

`AgentSession` is the easiest Python entry point for a single creature.
It wraps an `Agent` with a streaming chat interface and is also the same kind of surface used by higher-level serving layers.

```python
import asyncio

from kohakuterrarium.serving.agent_session import AgentSession


async def main() -> None:
    session = await AgentSession.from_path("@kt-defaults/creatures/general")

    try:
        async for chunk in session.chat("Summarize what this repository does."):
            print(chunk, end="", flush=True)
        print()
    finally:
        await session.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

Key points:

- `AgentSession.from_path(path)` loads and starts a creature from a filesystem path or package ref
- `session.chat(message)` returns an async iterator of streamed output chunks
- the creature keeps conversation context across calls
- always call `session.stop()` when you are done

### Build from config in memory

When you want to start from an existing config and override it in code:

```python
from kohakuterrarium.core.config import load_agent_config
from kohakuterrarium.serving.agent_session import AgentSession


async def create_custom_agent() -> AgentSession:
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name = "my-custom-agent"
    config.system_prompt = "You are a pirate. Respond in pirate speak."
    config.tools = []
    config.subagents = []

    return await AgentSession.from_config(config)
```

This is a good pattern when you want to inherit the structure of a real creature but specialize it dynamically.

## 2. Direct control with `Agent`

Use `Agent` when you want direct lifecycle control instead of the higher-level chat wrapper.

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    agent = Agent.from_path("@kt-defaults/creatures/swe")
    parts: list[str] = []

    agent.set_output_handler(lambda text: parts.append(text), replace_default=True)

    await agent.start()
    try:
        await agent.inject_input("Explain how this codebase is structured.")
        print("".join(parts))
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

Use this layer when you want:

- custom output handling
- direct event injection
- tighter lifecycle management
- integration with your own surrounding runtime
- explicit control of sessions, interruption, or model switching

Important methods include:

- `Agent.from_path(...)`
- `start()`
- `stop()`
- `inject_input(...)`
- `inject_event(...)`
- `interrupt()`
- `switch_model(...)`
- `set_output_handler(...)`

## 3. Terrarium from code

You can also start and control a terrarium directly from Python.

```python
import asyncio

from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime


async def main() -> None:
    config = load_terrarium_config("@kt-defaults/terrariums/swe_team")
    runtime = TerrariumRuntime(config)
    await runtime.start()

    try:
        tasks = runtime.environment.shared_channels.get("tasks")
        if tasks is not None:
            await tasks.send(
                ChannelMessage(sender="user", content="Fix the auth bug.")
            )

        await runtime.run()
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

Use `TerrariumRuntime` when you need:

- direct control over terrarium lifecycle
- programmatic channel interaction
- status inspection
- runtime-level coordination around a terrarium

Important surfaces include:

- `start()`
- `run()`
- `stop()`
- `get_status()`
- `environment`
- `api`
- `observer`
- `get_creature_agent(name)`

## 4. Service-style integration with `KohakuManager`

Use `KohakuManager` when you want a management layer above creatures and terrariums.

This is the right level for:

- backend services
- custom APIs
- dashboards or UI backends
- systems that create and manage many runtimes

```python
import asyncio

from kohakuterrarium.serving.manager import KohakuManager


async def main() -> None:
    manager = KohakuManager(session_dir="./sessions")

    agent_id = await manager.agent_create(config_path="@kt-defaults/creatures/general")
    try:
        async for chunk in manager.agent_chat(agent_id, "What tools do you have?"):
            print(chunk, end="")
    finally:
        await manager.agent_stop(agent_id)


if __name__ == "__main__":
    asyncio.run(main())
```

This layer also connects closely to the HTTP and WebSocket serving stack.

## 5. Composition algebra

Composition algebra is one of the strongest programmatic features in the framework.

It lets you treat creatures as composable programmatic steps and combine them with Python operators.

```python
from kohakuterrarium.compose import agent, factory
```

### `agent()` for persistent creatures

`agent()` creates a persistent runnable creature that keeps context across calls.

```python
async with await agent("@kt-defaults/creatures/general") as a:
    first = await a("Tell me a joke")
    second = await a("Tell me another in the same style")
```

Use this when you want memory across turns.

### `factory()` for ephemeral creatures

`factory()` creates a fresh creature per call.

```python
specialist = factory("@kt-defaults/creatures/general")
result = await specialist("Summarize this file")
```

Use this when you want disposable workers with no retained context.

### `>>` for pipelines

```python
pipeline = writer >> reviewer
pipeline = extractor >> json.loads >> formatter
pipeline = agent_a >> (lambda text: text.upper()) >> agent_b
```

### `&` for parallel branches

```python
results = await (analyst & reviewer & writer)(task)
```

### `|` for fallback

Use a fallback runnable if the primary raises.

### `*` for retry

Retry the same runnable multiple times.

### `async for` for iterative workflows

This is especially useful for review loops, debate, or refine-until-good-enough patterns.

## Example: write-review loop

```python
import asyncio
from kohakuterrarium.compose import agent


async def main() -> None:
    async with await agent("@kt-defaults/creatures/creative") as writer, \
               await agent("@kt-defaults/creatures/reviewer") as reviewer:
        pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer

        async for feedback in pipeline.iterate("Write a short product intro"):
            print(feedback)
            if "APPROVED" in feedback:
                break


if __name__ == "__main__":
    asyncio.run(main())
```

This is a good example of why programmatic usage is a first-class capability in KT: your application owns the loop, while creatures provide the agentic work.

## 6. Sessions and memory in code

Programmatic usage can still take advantage of session persistence and searchable history.

Relevant surfaces include:

```python
from kohakuterrarium.session.store import SessionStore
```

Use these when you want to:

- persist operational state
- inspect prior runs
- treat session history as searchable knowledge
- build custom tools around session memory

This matters because session history is not only for resume. It can also act as a knowledge database that supports FTS or vector-based retrieval.

## Choosing the right layer

### Use `AgentSession`

When you want the easiest streaming interface for one creature.

### Use `Agent`

When you want direct runtime control of one creature.

### Use `TerrariumRuntime`

When you want direct control of a terrarium runtime.

### Use `KohakuManager`

When you want a service or orchestration layer above creatures and terrariums.

### Use composition algebra

When your own application logic is the orchestrator and you want agents as composable steps.

## Related reading

- [Python API](../reference/python.md)
- [Creatures](creatures.md)
- [Terrariums](terrariums.md)
- [Sessions](sessions.md)
- [Serving Layer](../concepts/serving.md)
