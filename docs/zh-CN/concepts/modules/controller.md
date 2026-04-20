---
title: 控制器 (Controller)
summary: 从 LLM 串流、解析工具调用并派发回馈的推理回圈。
tags:
  - concepts
  - module
  - controller
---

# 控制器

## 它是什么

**控制器 (controller)** 是Creature的推理回圈。它从队列取出事件，要求
LLM 响应，派发返回的工具与子 Agent调用，收集结果，然后决定是否继续
回圈。

它*不是*「大脑」。大脑是 LLM。控制器是那层很薄的代码，负责让
LLM 真的能在时间中持续工作。

## 为什么它存在

LLM 是无状态的：你喂它消息，它吐回更多消息。Agent 是有状态的：它
有正在执行的工具、被派生出的子 Agent、持续进来的事件、逐步累积的回
合。总得有某个东西把两者桥接起来。

没有控制器的话，一个 Creature不是会坍缩成单次 LLM round-trip（聊天机器
人），就是每种 Agent 设计都得自己写一套胶水。控制器就是那个把
「LLM + 回圈 + 工具」变成可重用底层，而不是一次性拼装胶水的关键零件。

## 我们怎么定义它

把控制器的契约简化后，大致是：

```
loop:
  events = 从队列收集（可堆叠事件做 batch，遇到不可堆叠事件就中断）
  context = 从 events 建立这一回合的输入
  stream = LLM.chat(messages + context)
  for chunk in stream:
  输出文字 chunk
  派发解析出的 tool / sub-agent / framework-command 区块
  等待 direct-mode 工具与子 Agent完成
  把它们的结果作为新事件喂回去
  继续回圈或结束
```

有三个设计选择值得点名：

- **单一事件锁**。 每个 Creature同一时间只会跑一个 LLM 回合。触发器可
  以自由触发，但它们只会排进队列，不会中断当前回合。
- **可堆叠 batching**。 一阵相似事件突发时（例如同一个 tick 有两个
  工具完成），会合并成同一回合。
- **工具在串流中途派发**。 控制器不会等 LLM 整段说完才触发工具。
  见 [impl-notes/stream-parser](../impl-notes/stream-parser.md)。

## 我们怎么实现它

主要类别是 `Controller`（`core/controller.py`）。它持有事件用的
`asyncio.Queue`、LLM 输出串流的 parser 状态机，以及对Creature
`Registry`（工具）、`SubAgentManager`、`Executor` 与
`OutputRouter` 的参照。

关键不变条件：

- `_processing_lock` 会在整个「collect → stream → dispatch → await
  → loop」流程中持有。
- 不可堆叠事件（错误、优先讯号）会中断当前 batch，自己拿到独立回合。
- 控制器绝不直接调用工具；它会把工作交给 `Executor`，由后者产生
  `asyncio.Task`。

## 因此你可以做什么

- **在会话中途切换 LLM**。 `/model` 用户指令或 `switch_model`
  API 会原地切换 LLM provider。控制器不在乎自己正在和哪个 provider
  对话。
- **动态 system prompt**。 `update_system_prompt(...)` 可以在下一回
  合前追加或替换提示词；控制器会自动接手使用。
- **重生某一回合**。 `regenerate_last_response()` 会告诉控制器用当前
  状态重新执行上一个 LLM 调用。
- **从任何地方注入事件**。 因为一切都经过事件队列，plugin、工具，
  或外部 Python 程序都可以调用 `Agent.inject_event(...)`，控制器会
  按顺序处理它。

## 不要被它框住

没有控制器的 Creature是说不通的 — 没有回圈就没有 Agent。但回圈的*形状*
是可以谈的。plugin hook（`pre_llm_call`、`post_llm_call`、
`pre_tool_execute`、…）让你能从外部重写回圈中的每一步，而不必碰
`Controller` 类别本身。见 [插件](plugin.md)。

## 另见

- [组成一个 Agent](../foundations/composing-an-agent.md) — 控制器位在什么位置。
- [impl-notes/stream-parser](../impl-notes/stream-parser.md) — 为什么工具会在 LLM 停下前就开始。
- [impl-notes/prompt-aggregation](../impl-notes/prompt-aggregation.md) — 控制器实际在驱动的是哪一份提示词。
- [reference/python.md — Agent, Controller 参考](../../reference/python.md) — 签名。
