---
title: 插件 Hook 参考
summary: 插件可注册的所有生命周期 Hook、触发时机，以及收到的 payload。
tags:
  - reference
  - plugin
  - Hooks
---

# 插件 Hook 参考

本文整理了所有暴露给插件的生命周期、LLM、工具、子代理和回调 Hook。这些 Hook 由 `kohakuterrarium.modules.plugin` 中的 `Plugin` protocol 定义；`BasePlugin` 提供默认的空操作实现。实际接入点位于 `bootstrap/plugins.py`。

如需先了解心智模型，请参见 [插件概念](../concepts/modules/plugin.md)。如需任务导向的说明，请参见 [插件指南](../guides/plugins.md) 和 [自定义模块指南](../guides/custom-modules.md)。

## 返回值说明

- **转换型 Hook**（`pre_*`、`post_*`）：返回 `None` 表示保持原值不变；返回新值则会替换传给下一个插件或框架的值。
- **回调 Hook**（`on_*`）：返回值会被忽略；采用 fire-and-forget 模式。

## 阻断

任何 `pre_*` Hook 都可以抛出 `PluginBlockError` 来中断该操作。框架会将错误向外暴露，请求不会继续执行，且对应的 `post_*` Hook **不会** 触发。回调 Hook 不能阻断流程。

---

## 生命周期 Hook

| Hook | 签名 | 触发时机 | 返回值 |
|---|---|---|---|
| `on_load` | `async on_load(ctx: PluginContext) -> None` | 插件加载到 Agent 时。 | 忽略 |
| `on_unload` | `async on_unload() -> None` | 插件卸载时，或 Agent 停止时。 | 忽略 |

`PluginContext` 允许插件访问 Agent、其配置、scratchpad 和 logger。详细结构请参见 `kohakuterrarium.modules.plugin.context`。

---

## LLM Hook

| Hook | 签名 | 触发时机 | 返回值 |
|---|---|---|---|
| `pre_llm_call` | `async pre_llm_call(messages: list[dict], **kwargs) -> list[dict] \| None` | 每次 LLM 请求前触发（controller、sub-agent、compact 都会经过这里）。 | `None` 保留原消息；返回新的 list 则替换。可抛出 `PluginBlockError`。 |
| `post_llm_call` | `async post_llm_call(response: ChatResponse) -> ChatResponse \| None` | LLM 响应组装完成后。 | `None` 保留原响应；返回新的 `ChatResponse` 则替换。 |

---

## 工具 Hook

| Hook | 签名 | 触发时机 | 返回值 |
|---|---|---|---|
| `pre_tool_execute` | `async pre_tool_execute(name: str, args: dict) -> dict \| None` | 工具送入 executor 前。 | `None` 保留 `args`；返回新的 dict 则替换。可抛出 `PluginBlockError`。 |
| `post_tool_execute` | `async post_tool_execute(name: str, result: ToolResult) -> ToolResult \| None` | 工具完成后触发（包含错误结果）。 | `None` 保留结果；返回新的 `ToolResult` 则替换。 |

---

## 子代理 Hook

| Hook | 签名 | 触发时机 | 返回值 |
|---|---|---|---|
| `pre_subagent_run` | `async pre_subagent_run(name: str, ctx: SubAgentContext) -> dict \| None` | 子代理创建并启动前。 | `None` 保留启动上下文；返回 dict 会 merge 为覆盖值。可抛出 `PluginBlockError`。 |
| `post_subagent_run` | `async post_subagent_run(name: str, output: str) -> str \| None` | 子代理完成后触发（其输出即将以 `subagent_output` 事件送回）。 | `None` 保留输出；返回新字符串则替换。 |

---

## 回调 Hook

所有回调都采用 fire-and-forget 模式。返回值会被忽略。它们由插件调度器并行执行；慢速回调不会阻塞 agent。

| Hook | 签名 | 触发时机 |
|---|---|---|
| `on_tool_start` | `async on_tool_start(name: str, args: dict) -> None` | 工具即将开始执行。 |
| `on_tool_end` | `async on_tool_end(name: str, result: ToolResult) -> None` | 工具执行完成。 |
| `on_llm_start` | `async on_llm_start(messages: list[dict]) -> None` | LLM 请求送出时。 |
| `on_llm_end` | `async on_llm_end(response: ChatResponse) -> None` | LLM 响应返回时。 |
| `on_processing_start` | `async on_processing_start() -> None` | Agent 进入处理回合。 |
| `on_processing_end` | `async on_processing_end() -> None` | Agent 离开处理回合。 |
| `on_startup` | `async on_startup() -> None` | Agent `start()` 完成后。 |
| `on_shutdown` | `async on_shutdown() -> None` | Agent `stop()` 执行过程中。 |
| `on_compact_start` | `async on_compact_start(reason: str) -> None` | 开始 compact 时。 |
| `on_compact_complete` | `async on_compact_complete(summary: str) -> None` | compact 完成后。 |
| `on_event` | `async on_event(event: TriggerEvent) -> None` | 任意事件注入 controller 时。 |

---

## Prompt 插件（独立类别）

Prompt 插件会在 system prompt 组装过程中执行，实现位于 `prompt/aggregator.py`。它们与生命周期插件分开加载。

`BasePlugin`（位于 `kohakuterrarium.prompt.plugins`）包含：

```python
priority: int       # 越小越早
name: str
async def get_content(self, context: PromptContext) -> str | None
```

- `get_content(context) -> str | None`：返回要插入的文本块；返回 `None` 表示不提供内容。
- `priority`：排序键。内置插件大致分布在 50/45/40/30。

内置 Prompt 插件请参见 [内置模块参考](builtins.md#prompt-plugins)。

自定义 Prompt 插件同样通过 creature config 的 `plugins` 字段注册；框架会根据插件类是生命周期 `Plugin` protocol 的子类，还是 prompt `BasePlugin`，决定走哪套调度流程。

---

## 编写插件

最小生命周期插件示例：

```python
from kohakuterrarium.modules.plugin import BasePlugin, PluginBlockError

class GuardPlugin(BasePlugin):
    async def pre_tool_execute(self, name, args):
        if name == "bash" and "rm -rf" in args.get("command", ""):
            raise PluginBlockError("unsafe command")
        return None  # 保持 args 不变
```

在 creature config 中注册：

```yaml
plugins:
  - name: guard
    type: custom
    module: ./plugins/guard.py
    class: GuardPlugin
```

运行时可通过 `/plugin toggle guard`（参见 [内置模块参考](builtins.md#user-commands)）或 HTTP 的插件切换端点启用或停用。

---

## 延伸阅读

- 概念：[插件概念](../concepts/modules/plugin.md)、[模式概念](../concepts/patterns.md)
- 指南：[插件指南](../guides/plugins.md)、[自定义模块指南](../guides/custom-modules.md)
- 参考：[Python API 参考](python.md)、[配置参考](configuration.md)、[内置模块参考](builtins.md)
