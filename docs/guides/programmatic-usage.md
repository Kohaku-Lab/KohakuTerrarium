# Programmatic Usage

For readers embedding agents inside their own Python code.

A creature isn't a config file — the config describes one. A running creature is an async Python object: `Agent`. Everything in KohakuTerrarium is callable and awaitable, including terrariums and sessions. Your code is the orchestrator; agents are workers you invoke.

Concept primer: [agent as a Python object](../concepts/python-native/agent-as-python-object.md), [composition algebra](../concepts/python-native/composition-algebra.md).

## Four entry points

| Surface | Use when |
|---|---|
| `Agent` | You want full control: inject events, attach custom output handlers, manage lifecycle yourself. |
| `AgentSession` | Streaming chat wrapper: inject input, iterate output chunks. Good for bots and web UIs. |
| `TerrariumRuntime` | You have a terrarium config and want to run it. |
| `KohakuManager` | Multi-tenant server: many agents/terrariums managed by ID, transport-agnostic. |

For multi-agent Python pipelines without a terrarium, see [Composition](composition.md).

## `Agent` — full control

```python
import asyncio
from kohakuterrarium.core.agent import Agent

async def main():
    agent = Agent.from_path("@kt-defaults/creatures/swe")
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
    session = await AgentSession.from_path("@kt-defaults/creatures/swe")
    await session.start()
    async for chunk in session.chat("What does this do?"):
        print(chunk, end="")
    print()
    await session.stop()

asyncio.run(main())
```

`chat(message)` yields text chunks as the controller streams. Tool activity and sub-agent events are surfaced through the output module's activity callbacks — `AgentSession` focuses on the text stream; for richer events, use `Agent` + a custom output module.

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

## Terrarium from code

```python
import asyncio
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.core.channel import ChannelMessage

async def main():
    config = load_terrarium_config("@kt-defaults/terrariums/swe_team")
    runtime = TerrariumRuntime(config)
    await runtime.start()

    tasks = runtime.environment.shared_channels.get("tasks")
    await tasks.send(ChannelMessage(sender="user", content="Fix the auth bug."))

    await runtime.run()
    await runtime.stop()

asyncio.run(main())
```

Runtime methods: `start`, `stop`, `run`, `add_creature`, `remove_creature`, `add_channel`, `wire_channel`. The `environment` holds `shared_channels` (a `ChannelRegistry`) visible to all creatures; each creature has its own private `Session`.

## `KohakuManager` — multi-tenant

Used by the HTTP API, web app, and any code that wants "several agents, identified by ID":

```python
from kohakuterrarium.serving.manager import KohakuManager

manager = KohakuManager(session_dir="/var/kt/sessions")

agent_id = await manager.agent_create("@kt-defaults/creatures/swe")
async for chunk in manager.agent_chat(agent_id, "Hi"):
    print(chunk, end="")

status = manager.agent_status(agent_id)
manager.agent_interrupt(agent_id)
await manager.agent_stop(agent_id)
```

Also exposes terrarium/creature/channel operations. The manager takes care of session-store attachment and concurrent access safety.

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

For simple cases `AgentSession` / `KohakuManager` handle this automatically based on `session_dir`.

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
- **Hot-plugged creature never sees messages.** After `runtime.add_creature`, call `runtime.wire_channel(..., direction="listen")` for each channel the creature should consume.
- **`AgentSession.chat` hangs.** Another caller is using the agent; sessions serialize input. Use one `AgentSession` per caller.

## See also

- [Composition](composition.md) — Python-side multi-agent pipelines.
- [Custom Modules](custom-modules.md) — write the tools/inputs/outputs you wire in.
- [Reference / Python API](../reference/python.md) — exhaustive signatures.
- [examples/code/](../../examples/code/) — runnable scripts for each pattern.
