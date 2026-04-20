---
title: 插件
summary: Prompt 插件与 lifecycle 插件——各自挂在哪里、怎么组合，以及什么时候该用。
tags:
 - guides
 - plugin
 - extending
---

# 插件

给想在模块之间的*接缝*加上行为、又不想 fork 任何模块的读者。

插件修改的是 controller、工具、子代理与 LLM 之间的连接方式，而不是模块本身。分成两类： **prompt 插件 ** 会往 system prompt 塞内容， **lifecycle 插件** 则挂在执行时事件上（pre/post LLM、pre/post tool 等）。

概念先读：[plugin 概念](../concepts/modules/plugin.md)、[patterns 概念](../concepts/patterns.md)。

## 什么时候该写 plugin、tool 或 module

- *tool* 是 LLM 可以用名字调用的东西。
- *module*（input/output/trigger/sub-agent）是一整个执行时介面。
- *plugin* 是在它们*之间*执行的规则——像 guard、accounting、prompt injection、memory retrieval。

如果你的需求是「每次在 X 前后，都做 Y」，答案几乎总是 plugin。

## Prompt 插件

契约：

- 继承 `BasePlugin`。
- 设置 `name`、`priority`（数字越小，越早出现在最终 prompt）。
- 实作 `get_content(context) -> str | None`。

```python
# plugins/project_header.py
from kohakuterrarium.modules.plugin.base import BasePlugin

class ProjectHeaderPlugin(BasePlugin):
    name = "project_header"
    priority = 35          # 在 ProjectInstructionsPlugin (30) 之前

    def __init__(self, text: str = ""):
        super().__init__()
        self.text = text

    def get_content(self, context) -> str | None:
        if not self.text:
            return None
        return f"## Project Header\n\n{self.text}"
```

内置 prompt 插件（永远存在）：

| Plugin | Priority | 用途 |
|---|---|---|
| `ProjectInstructionsPlugin` | 30 | 加载 `CLAUDE.md` / `.claude/rules.md` |
| `EnvInfoPlugin` | 40 | 工作目录、平台和日期 |
| `FrameworkHintsPlugin` | 45 | 工具调用语法 + 框架命令示例（`info`、`jobs`、`wait`） |
| `ToolListPlugin` | 50 | 每个工具的一行描述 |

Priority 越低越早执行。你可以借此把插件插到正确位置。

## Lifecycle 插件

继承 `BasePlugin`，并实作以下任意 hook。全部都是 async。

| Hook | Signature | 效果 |
|---|---|---|
| `on_load(context)` | agent 启动时初始化 | — |
| `on_unload()` | 停止时清理 | — |
| `pre_llm_call(messages, ** kwargs)` | 回传 `list[dict] \| None` | 取代送往 LLM 的消息 |
| `post_llm_call(response)` | 回传 `ChatResponse \| None` | 取代回应 |
| `pre_tool_execute(name, args)` | 回传 `dict \| None`；或 raise `PluginBlockError` | 取代参数或阻挡调用 |
| `post_tool_execute(name, result)` | 回传 `ToolResult \| None` | 取代工具结果 |
| `pre_subagent_run(name, context)` | 回传 `dict \| None` | 取代子代理上下文 |
| `post_subagent_run(name, output)` | 回传 `str \| None` | 取代子代理输出 |

Fire-and-forget 回呼（没有回传值、也无法修改内容）：

- `on_tool_start`, `on_tool_end`
- `on_llm_start`, `on_llm_end`
- `on_processing_start`, `on_processing_end`
- `on_startup`, `on_shutdown`
- `on_compact_start`, `on_compact_complete`
- `on_event`

## 示例：tool guard

阻挡危险的 shell 命令。

```python
# plugins/tool_guard.py
from kohakuterrarium.modules.plugin.base import BasePlugin, PluginBlockError

class ToolGuard(BasePlugin):
    name = "tool_guard"

    def __init__(self, deny_patterns: list[str]):
        super().__init__()
        self.deny_patterns = deny_patterns

    async def pre_tool_execute(self, name: str, args: dict) -> dict | None:
        if name != "bash":
            return None
        command = args.get("command", "")
        for pat in self.deny_patterns:
            if pat in command:
                raise PluginBlockError(f"Blocked by tool_guard: {pat!r}")
        return None
```

设置：

```yaml
plugins:
  - name: tool_guard
    type: custom
    module: ./plugins/tool_guard.py
    class: ToolGuard
    options:
      deny_patterns: ["rm -rf /", "dd if=/dev/zero"]
```

丢出 `PluginBlockError` 会中止该操作——错误消息会成为工具结果。

## 示例：token accounting

```python
class TokenAccountant(BasePlugin):
    name = "token_accountant"

    async def post_llm_call(self, response):
        usage = response.usage or {}
        my_db.record(tokens_in=usage.get("prompt_tokens"),
                     tokens_out=usage.get("completion_tokens"))
        return None   # 不取代回应
```

## 示例：seamless memory（在插件里用 agent）

做一个 `pre_llm_call` 插件，先取回相关的历史事件，再把它们 prepend 到 messages 前面。你甚至可以调用一个小型巢状 agent 来判断哪些内容相关——plugin 就是普通 Python，所以里面用 agent 完全合法。可参考 [concepts/python-native/agent-as-python-object 概念](../concepts/python-native/agent-as-python-object.md)。

## 在执行时管理插件

Slash 指令：

```
/plugin list
/plugin enable tool_guard
/plugin disable tool_guard
/plugin toggle tool_guard
```

插件只会在 agent 启动时加载一次；enable/disable 只是执行时旗标，不是重新加载。若你修改了设置，仍然需要重启。

## 发布插件

打包进 package：

```yaml
# my-pack/kohaku.yaml
name: my-pack
plugins:
  - name: tool_guard
    module: my_pack.plugins.tool_guard
    class: ToolGuard
```

用户在自己的Creature中启用它：

```yaml
plugins:
  - name: tool_guard
    type: package
    options: { deny_patterns: [...] }
```

详见 [包指南](packages.md)。

## Hook 的执行顺序

当多个插件实作同一个 hook 时：

- `pre_*` hook 依注册顺序执行；第一个回传非 `None` 值的插件胜出。
- `post_*` hook 依注册顺序执行；每个插件都会收到上一个插件处理后的输出。
- Fire-and-forget hook 全都会执行（错误只记录，不往外抛）。

任何 `pre_*` hook 只要丢出 `PluginBlockError`，就会直接短路后续插件与该操作。

## 测试插件

```python
from kohakuterrarium.testing.agent import TestAgentBuilder

env = (
    TestAgentBuilder()
    .with_llm_script(["[/bash]@@command=rm -rf /\n[bash/]", "Stopped."])
    .with_builtin_tools(["bash"])
    .with_plugin(ToolGuard(deny_patterns=["rm -rf /"]))
    .build()
)
await env.inject("cleanup")
assert any("Blocked" in act[1] for act in env.output.activities)
```

## 疑难排解

- **找不到插件 class**。 检查 `class` 字段（不是 `class_name`——plugin 用的是 `class`）。设置加载器两者都接受，但 package manifest 用的是 `class`。
- **Hook 从来没触发**。 确认 hook 名称拼对；像 `pre_llm_call` 与 `pre_tool_execute` 若拼错，会静默失效。
- **`PluginBlockError` 抛出了，但调用还是执行了**。 你是在 `post_*` hook 里丢出的。要阻挡，请用 `pre_tool_execute`。
- **对顺序敏感的插件堆叠行为不正确**。 `pre_*` hook 依注册顺序执行；请调整设置中 `plugins:` 清单的顺序。

## 延伸阅读

- [examples/plugins/](../../examples/plugins/) — 每种 hook 类型各有一个示例。
- [自定义模块指南](custom-modules.md) — 编写插件所包围的那些模块。
- [参考 / plugin hooks](../reference/plugin-hooks.md) — 所有 hook 的完整 signature。
- [概念 / plugin](../concepts/modules/plugin.md) — 设计理由。
