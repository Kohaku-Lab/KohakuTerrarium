---
title: 自訂模組
summary: 依模組協定寫出自訂 input、trigger、tool、output、sub-agent，並註冊進設定。
tags:
  - guides
  - extending
  - module
---

# 自訂模組

給想要自己寫工具、輸入、輸出、觸發器、子代理的讀者。

KohakuTerrarium 每個可擴充的介面都是一個 Python 協定。你實作協定、在 config 指向你的模組，剩下框架會處理。不需要改框架原始碼。

觀念預備：[模組索引](../concepts/modules/README.md)，以及 `../concepts/modules/` 下每個模組各自的頁面。

## 自訂模組長什麼樣

每個模組就是一支 Python 檔 (放哪都可以 — 通常放在生物資料夾裡、或某個套件裡)。Config 用 `module: ./path/to/file.py` + `class: YourClass` 指過去。(每種模組的 YAML key 都是 `class`。外掛為了向後相容也接受 `class_name`；見 [外掛](plugins.md)。)

五種模組接線方式都一樣。差別只在實作哪個協定。

## 工具

契約 (`kohakuterrarium.modules.tool.base`)：

- `async execute(args: dict, context: ToolContext | None) -> ToolResult`
- 選用的類別屬性：`needs_context`、`parallel_allowed`、`timeout`、`max_output`
- 選用的 `get_full_documentation() -> str` (由 `info` 框架指令載入)

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
    class: MyTool
```

工具執行模式 (在 `BaseTool` 設)：

- **direct** (預設) — 在同一回合 await，結果變成 `tool_complete` 事件。
- **background** — 送出後回傳 job id，結果晚點再到。
- **stateful** — 類似 generator，跨回合 yield 中間結果。

測試：

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

## 輸入

契約 (`kohakuterrarium.modules.input.base`)：

- `async start()` / `async stop()`
- `async get_input() -> TriggerEvent | None`

當輸入用完時回傳 `None` (會觸發代理關閉)。

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
  class: LineFileInput
  options:
    path: ./tasks.txt
```

## 輸出

契約 (`kohakuterrarium.modules.output.base`)：

- `async start()`、`async stop()`
- `async write(content: str)` — 完整訊息
- `async write_stream(chunk: str)` — 串流 chunk
- `async flush()`
- `async on_processing_start()`、`async on_processing_end()`
- `def on_activity(activity_type: str, detail: str)` — 工具/子代理事件
- 選用：`async on_user_input(text)`、`async on_resume(events)`

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
  class: DiscordWebhookOutput
  options:
    webhook_url: "${DISCORD_WEBHOOK}"
```

或者當作一個 named 側通道 (主輸出還是 stdout，工具可以 route 到這裡)：

```yaml
output:
  type: stdout
  named_outputs:
    discord:
      type: custom
      module: ./outputs/discord.py
      class: DiscordWebhookOutput
      options: { webhook_url: "${DISCORD_WEBHOOK}" }
```

## 觸發器

契約 (`kohakuterrarium.modules.trigger.base`)：

- `async wait_for_trigger() -> TriggerEvent | None`
- 選用：`async _on_start()`、`async _on_stop()`
- 選用類別屬性：`resumable`、`universal`
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
    class: TimerTrigger
    options: { interval: 60 }
    prompt: "Check the dashboard."
```

`universal: True` 標記這個類別可以由代理自己 setup。在類別上填 `setup_tool_name`、`setup_description`、`setup_param_schema`、(選用的) `setup_full_doc`；在生物 config 的 `tools:` 下放一筆 `type: trigger` + `name: <setup_tool_name>`。框架會把這個類別包成一個以 `setup_tool_name` 為名的工具，呼叫它時就透過代理的 `TriggerManager` 在背景裝設觸發器。

## 子代理

子代理由 `SubAgentConfig` (一個 config dataclass) 定義 — 你很少需要直接繼承 `SubAgent`。通常的做法是寫一支 Python 模組、export 一個 config 物件：

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
    model="subagent-default",
    default_plugins=["auto-compact"],
    plugins=[
        {
            "name": "budget",
            "options": {
                "turn_budget": [40, 60],
                "tool_call_budget": [75, 100],
            },
        },
    ],
)
```

Config：

```yaml
subagents:
  - name: specialist
    type: custom
    module: ./subagents/specialist.py
    config: SPECIALIST_CONFIG
```

很多專家不需要 Python 模組。省略 `module` 和 `config`，即可在 YAML 中內聯同一份設定：

```yaml
subagents:
  - name: specialist
    type: custom
    description: Does niche analysis.
    system_prompt: "You analyze X. Return a short summary."
    tools: [read, grep]
    model: subagent-default
    default_plugins: ["auto-compact"]
    plugins:
      - name: budget
        options:
          turn_budget: [40, 60]
          tool_call_budget: [75, 100]
```

如果子代理要包另一個完整的自訂代理 (例如接別的框架，或純 Python 實作)，就繼承 `SubAgent` 實作 `async run(input_text) -> SubAgentResult`。見 [concepts/modules/sub-agent](../concepts/modules/sub-agent.md)。

## 打包自訂模組

放進一個套件裡：

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

其他 config 就能用 `type: package` 參照，框架會從 `my_pack.tools.my_tool:MyTool` 把 class 拉出來。

見 [套件](packages.md)。

## 測試自訂模組

`kohakuterrarium.testing` 的 `TestAgentBuilder` 會給你一隻完整代理，配好 `ScriptedLLM` 跟 `OutputRecorder`。你可以直接把模組注入進去：

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

觸發器的話：用 `EventRecorder` 驗證 `TriggerEvent` 的形狀。

## 疑難排解

- **Module not found。** `module:` 路徑是相對於生物資料夾。如果會有歧義就用絕對路徑。
- **工具沒出現在 prompt 裡。** 跑 `kt info path/to/creature`。八成是被默默拒絕了 — 確認 YAML 裡 `class:` 的值跟模組實際的 class 名稱有對上。(YAML key 是 `class`、不是 `class_name`；外掛為了向後相容也接 `class_name`，但 tool / input / output / trigger / 子代理都要用 `class`。)
- **`needs_context=True` 但測試裡 `context` 是 `None`。** `TestAgentBuilder` 會提供 context；如果要用頻道或草稿區，確認你有呼叫 `.with_session(...)`。
- **觸發器不會 resume。** 在類別設 `resumable = True` 並實作 `to_resume_dict()`。

## 延伸閱讀

- [外掛](plugins.md) — 模組之間**接縫**的行為 (pre/post hook)。
- [套件](packages.md) — 把模組打包出去重用。
- [Reference / Python API](../reference/python.md) — `BaseTool`、`BaseInputModule`、`BaseOutputModule`、`BaseTrigger`、`SubAgentConfig`。
- [概念 / 模組](../concepts/modules/README.md) — 每個模組一頁。
