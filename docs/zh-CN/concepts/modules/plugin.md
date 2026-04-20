---
title: 插件 (Plugin)
summary: 在不 fork 模块的前提下修改模块之间的连接方式——prompt plugins 与 lifecycle plugins。
tags:
  - concepts
  - module
  - plugin
---

# Plugin

## 它是什么

**plugin** 改的是 *模块之间的连接*，不是模块本身。模块是积木；plugin 则是跑在接缝上的东西。

它有两种 flavour，各自解决不同问题：

- **Prompt plugins**：在控制器建构 system prompt 时，往里面补内容。
- **Lifecycle plugins**：挂进运行时事件——例如 LLM 调用前后、工具调用前后、子 Agent 产生前后。

合起来看，plugin 是在 *不 fork 任何模块* 的前提下加行为的主要方式。

## 为什么它存在

大多数实用的 Agent 行为，既不是新工具，也不是新 LLM——而是一条跑在它们之间的规则。例如：

- 「每次 bash 调用前，都先用安全政策检查一次。」
- 「每次 LLM 调用后，都统计 token 方便计费。」
- 「每次 LLM 调用前，都把相关的历史事件捞出来注入消息里。」
- 「永远在 system prompt 前面加上一段专案专属指示。」

这些事情都可以靠 subclass 某个模块来做，但那样既侵入又脆弱——你 fork 了、上游改了、你就得 rebase。plugin 让你可以碰接缝，不必动积木。

## 我们怎么定义它

### Prompt plugins

一个 `BasePlugin` subclass，具备：

- `name` 与 `priority`（数值越低，越早出现在 prompt 里）
- `get_content(context) → str | None`，返回一段 prompt 文字（若返回 `None`，代表不提供任何内容）

聚合器（`prompt/aggregator.py`）会依照 priority 排序已注册的 plugins，然后把它们的输出串接成最终的 system prompt。

内建的有：`ToolListPlugin`（自动工具索引）、`FrameworkHintsPlugin`（如何调用工具／使用 `##commands##`）、`EnvInfoPlugin`（working dir、日期、平台）、`ProjectInstructionsPlugin`（加载 `CLAUDE.md` / `.claude/rules.md`）。

### Lifecycle plugins

一个 `BasePlugin` subclass，可以实现以下任意 hooks：

- `on_load(context)`, `on_unload()`
- `pre_llm_call(messages, ** kwargs) → list[dict] | None`
- `post_llm_call(response) → ChatResponse | None`
- `pre_tool_execute(name, args) → dict | None`
- `post_tool_execute(name, result) → ToolResult | None`
- `pre_sub-agent_run(name, context) → dict | None`
- `post_sub-agent_run(name, output) → str | None`
- Fire-and-forget：`on_tool_start`, `on_tool_end`, `on_llm_start`,
  `on_llm_end`, `on_processing_start`, `on_processing_end`,
  `on_startup`, `on_shutdown`, `on_compact_start`,
  `on_compact_complete`, `on_event`。

`pre_*` hook 可以丢出 `PluginBlockError("message")` 来终止操作——那段消息会变成工具结果，或是一个被阻挡的 `tool_complete` 事件。

## 我们怎么实现它

`PluginManager.notify(hook, **kwargs)` 会迭代所有已注册且已启用的 plugins，并依序 await 每一个有对应方法的实现。`bootstrap/plugins.py` 会在 Agent 启动时加载 config 宣告的 plugins；package 宣告的 plugins 则可透过 `kohaku.yaml` 被发现。

## 因此你可以做什么

- **安全护栏**。 用 `pre_tool_execute` plugin 拒绝危险指令。
- **Token 记帐**。 用 `post_llm_call` 统计 token 并写进外部储存。
- **无缝记忆**。 用 `pre_llm_call` 对历史事件做 embedding lookup，把相关上下文插到前面——本质上就是不透过工具调用，直接对 session history 做 RAG。
- **智慧护栏**。 用 `pre_tool_execute` plugin 跑一个小型的 *nested Agent*，判断某个动作能不能做。plugin 是 Python，Agent 也是 Python，所以这是合法的。参见 [patterns](../patterns.md)。
- **Prompt 组合**。 用 prompt plugin 注入由 scratchpad state 或 session metadata 动态推导出的指示。

## 不要被边界绑住

plugin 是可选的。没有 plugin 的 Creature 也能正常运作。但当你开始觉得「我需要一种遍布整个回圈的新行为」，答案几乎总是 plugin，而不是新模块。

## 延伸阅读

- [Controller](controller.md) — hooks 在哪里触发。
- [Prompt aggregation](../impl-notes/prompt-aggregation.md) — prompt plugins 怎么插进去。
- [Patterns — smart guard, seamless memory](../patterns.md) — plugin 里包 Agent。
- [reference/plugin-hooks.md 参考](../../reference/plugin-hooks.md) — 每个 hook 的签章。
