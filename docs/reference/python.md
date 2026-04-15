# Python API

This page is the high-level Python reference for the main public surfaces in KohakuTerrarium.

For the architecture behind these APIs, see [Concepts](../concepts/overview.md). For practical usage, see [Programmatic Usage](../guides/programmatic-usage.md).

## Main Python surfaces

Most users will interact with one of these layers:

| Surface | Use it when |
|---------|-------------|
| `AgentSession` | you want the simplest streaming interface for one creature |
| `Agent` | you want direct control of a single creature runtime |
| `TerrariumRuntime` | you want to run a terrarium directly in code |
| `KohakuManager` | you want a service-style manager above creatures and terrariums |
| config loaders | you want to inspect or build configs in code |
| session store APIs | you want persistence, resume integration, or searchable history |

## `AgentSession`

Import:

```python
from kohakuterrarium.serving.agent_session import AgentSession
```

Use `AgentSession` when you want the easiest Python entry point for a single creature.

Typical lifecycle:

```python
import asyncio

from kohakuterrarium.serving.agent_session import AgentSession


async def main() -> None:
    session = await AgentSession.from_path("@kt-defaults/creatures/general")
    try:
        async for chunk in session.chat("Summarize this repository."):
            print(chunk, end="")
    finally:
        await session.stop()


asyncio.run(main())
```

Key methods:

| Method | Purpose |
|--------|---------|
| `AgentSession.from_path(path, llm_override=None, pwd=None)` | create and start from a config path or package ref |
| `AgentSession.from_config(config)` | create and start from an in-memory config |
| `AgentSession.from_agent(agent)` | wrap an existing agent |
| `chat(message)` | stream output chunks for one injected input |
| `start()` | start the wrapped agent |
| `stop()` | stop the wrapped agent |
| `get_status()` | inspect current runtime status |

## `Agent`

Import:

```python
from kohakuterrarium.core.agent import Agent
```

Use `Agent` when you want direct runtime control over a single creature.

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
| `stop()` | stop the runtime and clean up |
| `inject_input(text, source=...)` | send input programmatically |
| `inject_event(event)` | inject a custom event |
| `interrupt()` | interrupt the current processing cycle |
| `switch_model(profile_name)` | switch model profile on the live agent |
| `set_output_handler(handler, replace_default=False)` | capture or replace output handling |
| `attach_session_store(store)` | attach persistence recording |
| `get_state()` | inspect runtime state |

Useful properties include:

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

Use `TerrariumRuntime` when you want direct access to the multi-creature runtime.

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

Common surfaces:

| Method / property | Purpose |
|-------------------|---------|
| `start()` | initialize the terrarium and its creatures |
| `run()` | run the terrarium event loop |
| `stop()` | stop all creatures and clean up |
| `get_status()` | inspect terrarium state |
| `environment` | access the shared terrarium environment |
| `api` | access the programmatic terrarium facade |
| `observer` | access channel observation helpers |
| `get_creature_agent(name)` | access an individual creature runtime |

## `KohakuManager`

Import:

```python
from kohakuterrarium.serving.manager import KohakuManager
```

Use `KohakuManager` when you want a service-style API that manages standalone creatures and terrariums from one place.

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

Important methods include:

### Agent lifecycle

| Method | Purpose |
|--------|---------|
| `agent_create(...)` | create and start a standalone creature |
| `register_agent(...)` | register a prebuilt agent |
| `agent_stop(agent_id)` | stop one creature |
| `agent_chat(agent_id, message)` | stream output from a creature |
| `agent_status(agent_id)` | get status for one creature |
| `agent_list()` | list running creatures |
| `agent_interrupt(agent_id)` | interrupt a creature turn |
| `agent_get_jobs(agent_id)` | inspect running or recent jobs |
| `agent_cancel_job(agent_id, job_id)` | cancel a running tool or sub-agent job |
| `agent_switch_model(agent_id, profile_name)` | switch the creature's model |
| `agent_execute_command(agent_id, command, args="")` | run a slash-style user command |

### Terrarium lifecycle

Representative terrarium methods include:

| Method | Purpose |
|--------|---------|
| `create_terrarium(...)` | create and start a terrarium |
| `stop_terrarium(terrarium_id)` | stop a terrarium |
| `get_terrarium_status(terrarium_id)` | inspect terrarium status |
| `terrarium_chat(terrarium_id, target, message)` | chat with a root or mounted creature |
| `send_to_channel(terrarium_id, channel, content, sender=...)` | inject work through a terrarium channel |
| `stream_channel_events(terrarium_id, channels=...)` | observe channel activity |
| `add_creature(...)` | hot-plug a creature |
| `add_channel(...)` | create a channel at runtime |
| `wire_channel(...)` | wire a creature to a channel |
| `shutdown()` | stop all managed runtimes |

This layer is closely related to the HTTP and WebSocket server.

## Config loading

### Load creature config

```python
from kohakuterrarium.core.config import load_agent_config
```

Use this when you want to inspect, inherit from, or modify creature configuration in code.

### Load terrarium config

```python
from kohakuterrarium.terrarium.config import load_terrarium_config
```

Use this when you want to inspect or validate terrarium topology in code.

## Sessions and persistence

Relevant modules include:

```python
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.resume import resume_agent, resume_terrarium
```

Use these when you want to:

- persist operational state
- resume previous work
- inspect stored conversations and events
- search prior history
- build tooling around session files

Session history is not only for resume. It can also act as a searchable knowledge store for prior work.

## Channels and messages

When interacting with a terrarium directly, you will often work with channel messages.

```python
from kohakuterrarium.core.channel import ChannelMessage
```

Typical pattern:

```python
tasks = runtime.environment.shared_channels.get("tasks")
if tasks is not None:
    await tasks.send(ChannelMessage(sender="user", content="Review this change."))
```

## Composition algebra

Programmatic composition lives under:

```python
from kohakuterrarium.compose import agent, factory
```

Use this layer when your application logic is the orchestrator and creatures are composable steps inside that orchestration.

## Extension-facing APIs

If you are implementing custom tools, inputs, outputs, triggers, or sub-agents, the main base protocols live under:

```python
kohakuterrarium.modules
```

That is the place to look when you are extending framework capability rather than only using the runtime.

See also:

- [Custom Modules](../guides/custom-modules.md)
- [Plugins](../guides/plugins.md)

## Choosing the right API layer

### Use `AgentSession`

When you want the easiest streaming interface for one creature.

### Use `Agent`

When you want direct runtime control of one creature.

### Use `TerrariumRuntime`

When you want a multi-creature runtime directly.

### Use `KohakuManager`

When you want a manager or service layer above creatures and terrariums.

### Use composition algebra

When you want your application code to orchestrate creatures as composable steps.

## Related reading

- [Programmatic Usage](../guides/programmatic-usage.md)
- [Creatures](../guides/creatures.md)
- [Terrariums](../guides/terrariums.md)
- [Sessions](../guides/sessions.md)
- [Serving Layer](../concepts/serving.md)
- [HTTP API](http.md)
