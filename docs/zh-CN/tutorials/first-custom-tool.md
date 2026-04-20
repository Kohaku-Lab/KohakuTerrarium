---
title: 第一个自定义工具
summary: 编写 Python 工具、注册它，并把它接入 Creature 配置。
tags:
  - tutorials
  - tool
  - extending
---

# 第一个自定义工具

**问题** ： 你的 Agent 需要内置工具未提供的能力。你想为它增加一个可由 LLM 调用的新函数。

**完成状态** ： 你将得到一个放在 Creature 目录中的 `BaseTool` 子类，并通过 `config.yaml` 接入配置，在运行时加载，并在收到请求时由 LLM 调用。

**前置条件** ： [第一个 Creature](first-creature.md)。你应该已经有一个属于自己的 Creature 目录。

这里的工具示例是一个非常简单的 `wordcount`，用于统计字符串中的单词数。重点在于结构，而不是逻辑本身。如果你想了解工具除了简单函数之外还 **可以** 是什么，请参阅 [工具概念](../concepts/modules/tool.md)。

## 第 1 步：选择目录

创建一个包含该工具的 Creature 目录。这里将它命名为 `creatures/tutorial-creature/`。工具源码会和配置文件放在一起：

```text
creatures/tutorial-creature/
  config.yaml
  prompts/
    system.md
  tools/
    wordcount.py
```

创建目录：

```bash
mkdir -p creatures/tutorial-creature/prompts
mkdir -p creatures/tutorial-creature/tools
```

## 第 2 步：编写工具

`creatures/tutorial-creature/tools/wordcount.py`：

```python
"""Word count tool — counts words in a given text."""

from typing import Any

from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)


class WordCountTool(BaseTool):
    """Count the words in a string."""

    @property
    def tool_name(self) -> str:
        return "wordcount"

    @property
    def description(self) -> str:
        # One line — goes straight into the system prompt.
        return "Count the words in a given piece of text."

    @property
    def execution_mode(self) -> ExecutionMode:
        # Pure, fast, in-memory — direct mode. See Step 5.
        return ExecutionMode.DIRECT

    # The JSON schema the LLM sees for args.
    parameters = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to count words in.",
            }
        },
        "required": ["text"],
    }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        text = args.get("text", "")
        count = len(text.split())
        return ToolResult(
            output=f"{count} words",
            metadata={"count": count},
        )
```

说明：

- 继承 `BaseTool`，实现 `tool_name`、`description` 和 `_execute`。`BaseTool` 公开的 `execute` 包装器已经处理好了 try / except，发生异常时会返回 `ToolResult(error=...)`。
- `parameters` 是与 JSON Schema 兼容的 dict。controller 会用它构造给 LLM 使用的工具 schema。
- `_execute` 是 async 函数，并且需要返回 `ToolResult`。`output` 可以是字符串，也可以是 `ContentPart` 列表，用于多模态结果。
- 如果工具需要工作目录、会话或 scratchpad，请在类上设置 `needs_context = True`，并在 `_execute` 中接收 `context` 关键字参数。完整的 `ToolContext` 接口请参阅 [工具概念](../concepts/modules/tool.md)。

## 第 3 步：把它接入 Creature 配置

`creatures/tutorial-creature/config.yaml`：

```yaml
name: tutorial_creature
version: "1.0"
base_config: "@kt-biome/creatures/general"

system_prompt_file: prompts/system.md

tools:
  - name: wordcount
    type: custom
    module: ./tools/wordcount.py
    class_name: WordCountTool
```

各字段的作用：

- `type: custom` —— 从本地 Python 文件加载，而不是 `builtin` 或 `package`。
- `module` —— `.py` 文件路径，会以 Agent 目录（`creatures/tutorial-creature/`）为相对基准解析。
- `class_name` —— 模块中的类名。

由于 `tools:` 会扩展继承来的列表，因此你会保留完整的 `general` 工具集，并在其基础上额外添加 `wordcount`。

`creatures/tutorial-creature/prompts/system.md`：

```markdown
# Tutorial Creature

You are a helpful assistant for text experiments. When a user asks
about word counts, prefer the `wordcount` tool.
```

## 第 4 步：运行并试用

```bash
kt run creatures/tutorial-creature --mode cli
```

给它一个提示：

```text
> Count the words in "hello world foo bar"
```

controller 应该会用 `text="hello world foo bar"` 调用 `wordcount`，并显示结果（`4 words`）。退出时，`kt` 会打印常规恢复提示。如果你想稳定看到它被触发，请使用新的会话，也可以加上 `--no-session` 做一次性运行。

## 第 5 步：选择正确的执行模式

工具有三种执行模式：

| 模式 | 何时使用 | 内置示例 |
|---|---|---|
| `DIRECT` | 快速、纯粹，并且能在当前轮次内完成。结果会在下一次 LLM 调用前等待完成。 | `wordcount`、`read`、`grep` |
| `BACKGROUND` | 执行时间较长（数秒以上）。会返回任务控制句柄；结果稍后以事件形式送达，LLM 可以继续工作。 | `bash`（长命令）、Sub-agent |
| `STATEFUL` | 多轮交互。工具会 yield，Agent 响应后，工具再继续 yield。 | 有状态精灵、REPL |

`BaseTool` 默认使用 `BACKGROUND`。如果这个默认值不合适，请像示例中那样覆写 `execution_mode`。纯计算、耗时低于 100ms 的工具应设为 `DIRECT`。

执行管线位于 [工具概念 —— 我们如何实现](../concepts/modules/tool.md#how-we-implement-it)。流式输出在解析到结束块后会立即启动工具；多个 `DIRECT` 工具会通过 `asyncio.gather` 并行执行。

## 第 6 步：使用 ScriptedLLM 测试它（可选）

在单元测试中，你可以使用可复现的 LLM 来驱动 controller。`kohakuterrarium.testing` 包内置了几个辅助工具：

```python
import asyncio

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.testing.llm import ScriptedLLM, ScriptEntry


async def test_wordcount() -> None:
    agent = Agent.from_path("creatures/tutorial-creature")
    agent.llm = ScriptedLLM([
        ScriptEntry('[/wordcount]{"text": "one two three"}[wordcount/]'),
        ScriptEntry("Done — 3 words."),
    ])

    await agent.start()
    try:
        await agent.inject_input("count words in 'one two three'")
    finally:
        await agent.stop()


asyncio.run(test_wordcount())
```

脚本中的工具调用语法取决于该 Creature 的 `tool_format`（`bracket` / `xml` / `native`）。如果是 native function calling，请使用对应 provider 的调用格式；如果是 `bracket`（SWE Creature 祖先的默认值），则使用 `[/name]{json}[name/]`。

`OutputRecorder`、`EventRecorder` 和 `TestAgentBuilder` 可参考 `src/kohakuterrarium/testing/`。

## 你学到了什么

- 工具本质上是一个 `BaseTool` 子类，包含 `tool_name`、`description`、`parameters` 和 `_execute`。
- `config.yaml` 中的 `tools:` 通过 `type: custom`、`module:` 和 `class_name:` 把它接入系统。
- 执行模式非常重要：快速且纯粹的工作适合 `DIRECT`，耗时较长的工作适合 `BACKGROUND`。
- 测试时可以用 `ScriptedLLM` 以可复现方式驱动整个流程。

## 接下来读什么

- [工具概念](../concepts/modules/tool.md) —— 工具 **可以** 是什么（消息总线、状态句柄、Agent 包装器等）。
- [自定义模块指南](../guides/custom-modules.md) —— 一起了解工具、Sub-agent、Trigger 和 Output。
- [第一个插件](first-plugin.md) —— 当你需要的行为发生在模块之间的接缝，而不是某个单独模块内部时。
