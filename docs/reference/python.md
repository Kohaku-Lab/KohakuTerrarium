# Python API

This page is the high-level Python reference for the main public surfaces in KohakuTerrarium.

For the architecture behind these APIs, see [Concepts](../concepts/overview.md). For practical usage, see [Programmatic Usage](../guides/programmatic-usage.md).

## Main Python surfaces

Most users will interact with one of these layers:

| Surface | Use it when |
|---------|-------------|
| `Agent` | you want to run or embed a single creature |
| `TerrariumRuntime` | you want to run a multi-creature system directly |
| `KohakuManager` | you want a service-style management layer above agents and terrariums |
| config loaders | you want to inspect or build configs in code |
| session store APIs | you want persistence and resume integration |

## `Agent`

Import:

```python
from kohakuterrarium.core.agent import Agent
```

Use `Agent` when you want to embed a single creature inside your own Python process.

Typical lifecycle:

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    agent = Agent.from_path("@kt-defaults/creatures/swe")

    await agent.start()
    try:
        await agent.inject_input("Summarize what this repository does.")
    finally:
        await agent.stop()


asyncio.run(main())
```

Key methods:

| Method | Purpose |
|--------|---------|
| `Agent.from_path(path, ...)` | build an agent from a config path or package reference |
| `start()` | initialize modules and runtime state |
| `run()` | enter the main event loop |
| `stop()` | stop the runtime and cleanup |
| `inject_input(text, source=...)` | send input programmatically |
| `inject_event(event)` | inject a custom event |
| `interrupt()` | interrupt the current processing cycle |
| `switch_model(profile_name)` | switch model profile on the live agent |
| `set_output_handler(handler, replace_default=False)` | capture or replace output handling |
| `attach_session_store(store)` | attach persistence recording |
| `get_state()` | inspect runtime state |

Useful properties:

- `is_running`
- `tools`
- `subagents`
- `conversation_history`

## `TerrariumRuntime`

Import:

```python
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.terrarium.config import load_terrarium_config
```

Use `TerrariumRuntime` when you want to work directly with the multi-agent runtime.

Typical lifecycle:

```python
import asyncio

from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime


async def main() -> None:
    config = load_terrarium_config("@kt-defaults/terrariums/swe_team")
    runtime = TerrariumRuntime(config)

    await runtime.start()
    try:
        await runtime.run()
    finally:
        await runtime.stop()


asyncio.run(main())
```

Use it when you need:

- direct control over terrarium lifecycle
- programmatic channel interaction
- runtime status inspection
- hot-plug style operations in code

Common operations:

| Method / property | Purpose |
|-------------------|---------|
| `start()` | initialize the terrarium and its creatures |
| `run()` | run the terrarium event loop |
| `stop()` | stop all creatures and cleanup |
| `get_status()` | inspect terrarium state |
| `environment` | access shared terrarium environment |
| `api` | access the programmatic terrarium facade |
| `observer` | access channel observation |

## `KohakuManager`

Import:

```python
from kohakuterrarium.serving.manager import KohakuManager
```

Use `KohakuManager` when you want a service-style API that manages standalone creatures and terrariums from one place.

It is the right choice when you are building:

- a custom backend
- a worker service
- your own API layer
- a UI on top of the framework

Typical example:

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


asyncio.run(main())
```

This layer is closely related to the HTTP and WebSocket server.

## Config loading

### Load creature config

```python
from kohakuterrarium.core.config import load_agent_config
```

Use this when you want to inspect or validate creature configuration in code.

### Load terrarium config

```python
from kohakuterrarium.terrarium.config import load_terrarium_config
```

Use this when you want to inspect or validate terrarium topology in code.

## Sessions and persistence

The framework includes session storage and resume support for both creatures and terrariums.

Important modules include:

```python
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.resume import resume_agent, resume_terrarium
```

Use these when you want to:

- persist operational state
- resume previous work
- inspect stored conversations and events
- build tooling around session files

## Channels and messages

When interacting with terrarium runtime directly, you will often work with channel messages.

```python
from kohakuterrarium.core.channel import ChannelMessage
```

Typical pattern:

```python
tasks = runtime.environment.shared_channels.get("tasks")
if tasks is not None:
    await tasks.send(ChannelMessage(sender="user", content="Review this change."))
```

## Extension-facing APIs

If you are implementing custom tools, inputs, outputs, triggers, or sub-agents, the main base protocols live under:

```python
kohakuterrarium.modules
```

That is the place to look when you are extending framework capability rather than just using the runtime.

See also:

- [Custom Modules](../guides/custom-modules.md)
- [Plugins](../guides/plugins.md)

## Choosing the right API layer

Use this rule of thumb:

### Use `Agent`

When you need one creature.

### Use `TerrariumRuntime`

When you need a multi-creature topology.

### Use `KohakuManager`

When you need a management or service layer above both.

## Related reading

- [Programmatic Usage](../guides/programmatic-usage.md)
- [Creatures](../guides/creatures.md)
- [Terrariums](../guides/terrariums.md)
- [HTTP API](http.md)
- [Serving Layer](../concepts/serving.md)
