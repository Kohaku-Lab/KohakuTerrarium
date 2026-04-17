# Custom Modules

For readers writing their own tools, inputs, outputs, triggers, or sub-agents.

Every extensible surface in KohakuTerrarium is a Python protocol. You implement the protocol, point config at your module, the framework does the rest. No framework source edits required.

Concept primer: [modules](../concepts/modules/README.md), and the per-module pages under `../concepts/modules/`.

## Shape of a custom module

Each module lives in a Python file (anywhere you like — usually under your creature folder or inside a package). The config points at `module: ./path/to/file.py` + `class_name: YourClass`.

All five module kinds use the same wiring pattern. They differ only in the protocol their class implements.

## Tools

Contract (`kohakuterrarium.modules.tool.base`):

- `async execute(args: dict, context: ToolContext | None) -> ToolResult`
- optional class attrs: `needs_context`, `parallel_allowed`, `timeout`, `max_output`
- optional `get_full_documentation() -> str` (loaded by the `info` framework command)

Minimal tool:

```python
# tools/my_tool.py
from kohakuterrarium.modules.tool.base import BaseTool, ToolContext, ToolResult


class MyTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="my_tool",
            description="Do the thing.",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                },
                "required": ["target"],
            },
            needs_context=True,
        )

    async def execute(self, args: dict, context: ToolContext | None = None) -> ToolResult:
        target = args["target"]
        # context.pwd, context.session, context.environment, context.file_guard, ...
        return ToolResult(output=f"Did the thing to {target}.")
```

Config:

```yaml
tools:
  - name: my_tool
    type: custom
    module: ./tools/my_tool.py
    class_name: MyTool
```

Tool execution modes (set via `BaseTool`):

- **direct** (default) — awaited within the turn, result becomes a `tool_complete` event.
- **background** — submitted, returns a job id; result arrives later.
- **stateful** — generator-like, yields intermediate results over multiple turns.

Testing:

```python
from kohakuterrarium.testing.agent import TestAgentBuilder
env = (
    TestAgentBuilder()
    .with_llm_script(["[/my_tool]@@target=x\n[my_tool/]", "Done."])
    .with_tool(MyTool())
    .build()
)
await env.inject("do it")
assert "Did the thing to x" in env.output.all_text
```

## Inputs

Contract (`kohakuterrarium.modules.input.base`):

- `async start()` / `async stop()`
- `async get_input() -> TriggerEvent | None`

Return `None` when input is exhausted (triggers agent shutdown).

```python
# inputs/line_file.py
import asyncio
import aiofiles
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.modules.input.base import BaseInputModule


class LineFileInput(BaseInputModule):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self._lines: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async with aiofiles.open(self.path) as f:
            async for line in f:
                await self._lines.put(line.strip())
        await self._lines.put(None)  # sentinel

    async def get_input(self) -> TriggerEvent | None:
        line = await self._lines.get()
        if line is None:
            return None
        return create_user_input_event(line, source="line_file")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
```

Config:

```yaml
input:
  type: custom
  module: ./inputs/line_file.py
  class_name: LineFileInput
  options:
    path: ./tasks.txt
```

## Outputs

Contract (`kohakuterrarium.modules.output.base`):

- `async start()`, `async stop()`
- `async write(content: str)` — complete message
- `async write_stream(chunk: str)` — streaming chunk
- `async flush()`
- `async on_processing_start()`, `async on_processing_end()`
- `def on_activity(activity_type: str, detail: str)` — tool/subagent events
- optional `async on_user_input(text)`, `async on_resume(events)`

```python
# outputs/discord.py
import httpx
from kohakuterrarium.modules.output.base import BaseOutputModule


class DiscordWebhookOutput(BaseOutputModule):
    def __init__(self, webhook_url: str):
        super().__init__()
        self.webhook_url = webhook_url
        self._buf: list[str] = []

    async def start(self) -> None:
        self._client = httpx.AsyncClient()

    async def stop(self) -> None:
        await self._client.aclose()

    async def write(self, content: str) -> None:
        await self._client.post(self.webhook_url, json={"content": content})

    async def write_stream(self, chunk: str) -> None:
        self._buf.append(chunk)

    async def flush(self) -> None:
        if self._buf:
            await self.write("".join(self._buf))
            self._buf.clear()

    async def on_processing_start(self) -> None: ...
    async def on_processing_end(self) -> None:
        await self.flush()

    def on_activity(self, activity_type: str, detail: str) -> None:
        pass
```

Config:

```yaml
output:
  type: custom
  module: ./outputs/discord.py
  class_name: DiscordWebhookOutput
  options:
    webhook_url: "${DISCORD_WEBHOOK}"
```

Or as a named side-channel (main output stays stdout, tools can route here):

```yaml
output:
  type: stdout
  named_outputs:
    discord:
      type: custom
      module: ./outputs/discord.py
      class_name: DiscordWebhookOutput
      options: { webhook_url: "${DISCORD_WEBHOOK}" }
```

## Triggers

Contract (`kohakuterrarium.modules.trigger.base`):

- `async wait_for_trigger() -> TriggerEvent | None`
- optional `async _on_start()`, `async _on_stop()`
- optional class attrs: `resumable`, `universal`
- if `resumable`: `to_resume_dict()` / `from_resume_dict()`

Minimal timer:

```python
# triggers/timer.py
import asyncio
from kohakuterrarium.modules.trigger.base import BaseTrigger
from kohakuterrarium.core.events import TriggerEvent


class TimerTrigger(BaseTrigger):
    resumable = True

    def __init__(self, interval: float, prompt: str | None = None):
        super().__init__(prompt=prompt)
        self.interval = interval

    async def wait_for_trigger(self) -> TriggerEvent | None:
        await asyncio.sleep(self.interval)
        return self._create_event("timer", f"Timer fired after {self.interval}s")

    def to_resume_dict(self) -> dict:
        return {"interval": self.interval, "prompt": self.prompt}
```

Config:

```yaml
triggers:
  - type: custom
    module: ./triggers/timer.py
    class_name: TimerTrigger
    options: { interval: 60 }
    prompt: "Check the dashboard."
```

`universal: True` lets the agent install this trigger at runtime via the `create_trigger` tool.

## Sub-agents

Sub-agents are defined by `SubAgentConfig` (a config dataclass) — you rarely subclass `SubAgent` directly. The usual pattern is to ship a Python module exporting a config object:

```python
# subagents/specialist.py
from kohakuterrarium.modules.subagent.config import SubAgentConfig

SPECIALIST_CONFIG = SubAgentConfig(
    name="specialist",
    description="Does niche analysis.",
    system_prompt="You analyze X. Return a short summary.",
    tools=["read", "grep"],
    interactive=False,
    can_modify=False,
    llm="claude-haiku",
)
```

Config:

```yaml
subagents:
  - name: specialist
    type: custom
    module: ./subagents/specialist.py
    config_name: SPECIALIST_CONFIG
```

For a sub-agent that wraps an entire custom agent (e.g. a different framework, or a Python-first implementation), subclass `SubAgent` and implement `async run(input_text) -> SubAgentResult`. See [concepts/modules/sub-agent](../concepts/modules/sub-agent.md).

## Packaging custom modules

Drop them inside a package:

```
my-pack/
  kohaku.yaml
  my_pack/
    __init__.py
    tools/my_tool.py
    plugins/my_plugin.py
  creatures/
    my-agent/
      config.yaml
```

`kohaku.yaml`:

```yaml
name: my-pack
version: "0.1.0"
creatures: [{ name: my-agent }]
tools:
  - name: my_tool
    module: my_pack.tools.my_tool
    class: MyTool
python_dependencies:
  - httpx>=0.27
```

Now other configs can reference `type: package` and the framework pulls the class from `my_pack.tools.my_tool:MyTool`.

See [Packages](packages.md).

## Testing custom modules

`TestAgentBuilder` from `kohakuterrarium.testing` gives you a full agent with a `ScriptedLLM` and an `OutputRecorder`. You can inject your module directly:

```python
from kohakuterrarium.testing.agent import TestAgentBuilder

env = (
    TestAgentBuilder()
    .with_llm_script([...])
    .with_tool(MyTool())
    .build()
)
await env.inject("...")
assert env.output.all_text == "..."
```

For triggers: use `EventRecorder` and verify `TriggerEvent` shape.

## Troubleshooting

- **Module not found.** `module:` paths are relative to the creature folder. Use absolute paths if that's ambiguous.
- **Tool isn't in the prompt.** Check `kt info path/to/creature`. The tool is probably silently rejected — confirm `class_name` matches.
- **`needs_context=True` but `context` is `None` in tests.** `TestAgentBuilder` provides a context; make sure you called `.with_session(...)` if you need channels or scratchpad.
- **Trigger doesn't resume.** Set `resumable = True` on the class and implement `to_resume_dict()`.

## See also

- [Plugins](plugins.md) — for behaviour at the *seams* between modules (pre/post hooks).
- [Packages](packages.md) — shipping modules for reuse.
- [Reference / Python API](../reference/python.md) — `BaseTool`, `BaseInputModule`, `BaseOutputModule`, `BaseTrigger`, `SubAgentConfig`.
- [Concepts / modules](../concepts/modules/README.md) — one page per module.
