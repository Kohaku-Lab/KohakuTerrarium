---
title: 自定义模块
summary: 依模块协定写出自定义 input、trigger、tool、output、sub-agent，并注册进设置。
tags:
 - guides
 - extending
 - module
---

# 自定义模块

给想要自己写工具、输入、输出、触发器、子代理的读者。

KohakuTerrarium 每个可扩展的介面都是一个 Python 协定。你实作协定、在 config 指向你的模块，剩下框架会处理。不需要改框架原始码。

相关概念：[模块索引](../concepts/modules/README.md)，以及 `../concepts/modules/` 下每个模块各自的页面。

## 自定义模块长什么样

每个模块就是一支 Python 档 (放哪都可以 — 通常放在Creature目录里、或某个包里)。Config 用 `module: ./path/to/file.py` + `class_name: YourClass` 指过去。

五种模块接线方式都一样。差别只在实作哪个协定。

## 工具

契约 (`kohakuterrarium.modules.tool.base`)：

- `async execute(args: dict, context: ToolContext | None) -> ToolResult`
- 选用的类别属性：`needs_context`、`parallel_allowed`、`timeout`、`max_output`
- 选用的 `get_full_documentation() -> str` (由 `info` 框架指令加载)

最小工具：

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
        # context.pwd、context.session、context.environment、context.file_guard…
        return ToolResult(output=f"Did the thing to {target}.")
```

Config：

```yaml
tools:
  - name: my_tool
    type: custom
    module: ./tools/my_tool.py
    class_name: MyTool
```

工具执行模式 (在 `BaseTool` 设)：

- **direct** (默认) — 在同一回合 await，结果变成 `tool_complete` 事件。
- **background** — 送出后回传 job id，结果晚点再到。
- **stateful** — 类似 generator，跨回合 yield 中间结果。

测试：

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

## 输入

契约 (`kohakuterrarium.modules.input.base`)：

- `async start()` / `async stop()`
- `async get_input() -> TriggerEvent | None`

当输入用完时回传 `None` (会触发代理关闭)。

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

Config：

```yaml
input:
  type: custom
  module: ./inputs/line_file.py
  class_name: LineFileInput
  options:
    path: ./tasks.txt
```

## 输出

契约 (`kohakuterrarium.modules.output.base`)：

- `async start()`、`async stop()`
- `async write(content: str)` — 完整消息
- `async write_stream(chunk: str)` — 串流 chunk
- `async flush()`
- `async on_processing_start()`、`async on_processing_end()`
- `def on_activity(activity_type: str, detail: str)` — 工具/子代理事件
- 选用：`async on_user_input(text)`、`async on_resume(events)`

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

Config：

```yaml
output:
  type: custom
  module: ./outputs/discord.py
  class_name: DiscordWebhookOutput
  options:
    webhook_url: "${DISCORD_WEBHOOK}"
```

或者当作一个 named 侧通道 (主输出还是 stdout，工具可以 route 到这里)：

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

## 触发器

契约 (`kohakuterrarium.modules.trigger.base`)：

- `async wait_for_trigger() -> TriggerEvent | None`
- 选用：`async _on_start()`、`async _on_stop()`
- 选用类别属性：`resumable`、`universal`
- 若 `resumable`：`to_resume_dict()` / `from_resume_dict()`

最小的 timer：

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

Config：

```yaml
triggers:
  - type: custom
    module: ./triggers/timer.py
    class_name: TimerTrigger
    options: { interval: 60 }
    prompt: "Check the dashboard."
```

`universal: True` 标记这个类别可以由代理自己 setup。在类别上填 `setup_tool_name`、`setup_description`、`setup_param_schema`、(选用的) `setup_full_doc`；在Creature config 的 `tools:` 下放一笔 `type: trigger` + `name: <setup_tool_name>`。框架会把这个类别包成一个以 `setup_tool_name` 为名的工具，调用它时就通过代理的 `TriggerManager` 在背景装设触发器。

## 子代理

子代理由 `SubAgentConfig` (一个 config dataclass) 定义 — 你很少需要直接继承 `SubAgent`。通常的做法是写一支 Python 模块、export 一个 config 物件：

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

Config：

```yaml
subagents:
  - name: specialist
    type: custom
    module: ./subagents/specialist.py
    config_name: SPECIALIST_CONFIG
```

如果子代理要包另一个完整的自定义代理 (例如接别的框架，或纯 Python 实作)，就继承 `SubAgent` 实作 `async run(input_text) -> SubAgentResult`。见 [概念 / Sub-agent](../concepts/modules/sub-agent.md)。

## 打包自定义模块

放进一个包里：

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

`kohaku.yaml`：

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

其他 config 就能用 `type: package` 参照，框架会从 `my_pack.tools.my_tool:MyTool` 把 class 拉出来。

见 [包指南](packages.md)。

## 测试自定义模块

`kohakuterrarium.testing` 的 `TestAgentBuilder` 会给你一只完整代理，配好 `ScriptedLLM` 跟 `OutputRecorder`。你可以直接把模块注入进去：

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

触发器的话：用 `EventRecorder` 验证 `TriggerEvent` 的形状。

## 疑难排解

- **Module not found**。 `module:` 路径是相对于Creature目录。如果会有歧义就用绝对路径。
- **工具没有出现在 prompt 里**。 跑 `kt info path/to/creature`。八成是被默默拒绝了 — 确认 `class_name` 有对上。
- **`needs_context=True` 但测试里 `context` 是 `None`**。 `TestAgentBuilder` 会提供 context；如果要用频道或草稿区，确认你有调用 `.with_session(...)`。
- **触发器不会 resume**。 在类别将 `resumable = True` 并实作 `to_resume_dict()`。

## 延伸阅读

- [插件指南](plugins.md) — 模块之间 **接缝** 的行为 (pre/post hook)。
- [包指南](packages.md) — 把模块打包出去重用。
- [Reference / Python API 参考](../reference/python.md) — `BaseTool`、`BaseInputModule`、`BaseOutputModule`、`BaseTrigger`、`SubAgentConfig`。
- [概念 / 模块](../concepts/modules/README.md) — 每个模块一页。
