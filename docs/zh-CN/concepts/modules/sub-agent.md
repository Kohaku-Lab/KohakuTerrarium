---
title: 子 Agent
summary: 由父Creature为界定范围的任务所派生的嵌套Creature，拥有自己的上下文与一部分工具。
tags:
  - concepts
  - module
  - sub-agent
---

# 子 Agent

## 它是什么

**子 Agent (sub-agent)** 是由父Creature为某个界定范围的任务所派生出的嵌套Creature。它有自己的 LLM 对话、自己的工具（通常是父Creature工具的子集），以及自己的（较小）上下文。当它完成工作后，会返回一份浓缩结果，然后消失。

投影片版的总结是：*其实它也是一种工具*。从父控制器的角度来看，调用子 Agent和调用其他工具看起来完全一样。

## 为什么它存在

上下文视窗是有限的。真实任务——例如「探索这个 repo，然后告诉我 auth 是怎么运作的」——可能会牵涉上百次读档。如果把这些探索都放在父Creature自己的对话里，就会把主要工作的预算吃光。改由子 Agent去做，通常会消耗另一份预算，而返回的只是一份摘要。

这份预算现在可以配置。子 Agent可以有自己的多轴运行时预算（turn、工具调用，以及可选的 walltime），也可以不设限制，或共享父级的旧式 iteration budget。内置子 Agent会带一组保守的最小运行时预算：turn 软/硬限制 `40/60`、工具调用软/硬限制 `75/100`，并且没有 walltime 限制。

第二个理由是： **专门化**。一个专门为审查决策而提示的 `critic` 子 Agent，通常会比让一般 Agent 顺手兼做 review 来得更好。子 Agent让你可以把专家接进通才型工作流，而不用重写那个通才。

## 我们怎么定义它

子 Agent = 一份Creature配置 + 一个父层 registry。当它被派生时：

- 它会继承父Creature的 LLM 与工具格式；
- 它会拿到一部分工具（定义于子 Agent配置中的 `tools` 清单）；
- 它会跑完整的 Agent 生命周期（start → event-loop → stop）；
- 它可以有自己的统一 `budget` 插件选项（`turn_budget`、`tool_call_budget`，以及可选的 `walltime_budget`）；
- 它也可以继承父级旧式 iteration budget、拿到自己的 `budget_allocation`，或完全不共享预算；
- 它的结果会以父层上的 `subagent_output` 事件送达，
  或在 `output_to: external` 时直接串流给用户。

有三种重要型态：

- **One-shot**（预设）——派生后执行到完成，只返回一次。
- **输出型子 Agent**（`output_to: external`）——它的文字会和控制器的文字并行（或取而代之）串流到父Creature的 `OutputRouter`。你可以把它想成：控制器在背后默默协调；真正让用户读到的是子 Agent。
- **互动型**（`interactive: true`）——跨多轮持续存在，会接收上下文更新，也能被喂入新提示。适合那些能从对话连续性中受益的专家（持续运作的 reviewer、长驻 planner）。

## 我们怎么实现它

`SubAgentManager`（`modules/sub-agent/manager.py`）会把 `SubAgent`（`modules/sub-agent/base.py`）派生成 `asyncio.Task`，依 job id 追踪它们，并把完成结果作为 `TriggerEvent` 送出。

深度由 `max_subagent_depth`（配置层级）限制，以防止递回失控。取消采合作式机制——父Creature可以调用 `stop_task` 中断正在执行的子 Agent。

运行时预算由统一的 `budget` 插件执行，并通过 `plugins[].options` 配置 `turn_budget`、`tool_call_budget` 以及可选的 `walltime_budget`。自动压缩单独通过 `auto-compact` 插件包启用（它展开为 `compact.auto`）。旧式共享 iteration budget 在派生时解析：`budget_allocation` 优先，否则 `budget_inherit: true` 会在存在父级预算时复用同一个预算对象。

内建子 Agent（位于 `kt-biome` + framework）：`worker`、`plan`、`explore`、`critic`、`response`、`research`、`summarize`、`memory_read`、`memory_write`、`coordinator`。

## 因此你可以做什么

- **规划 / 实现 / 审查**。 一个父Creature配三个子 Agent。父Creature负责协调；每个子 Agent专注在单一阶段。
- **静默控制器**。 父Creature对 `response` 子 Agent使用 `output_to: external`。控制器本身不输出文字；只有子 Agent的回复会到达用户。这就是多数 kt-biome 聊天型Creature的工作方式。
- **常驻专家**。 一个 `interactive: true` 的 reviewer，看见每一轮，只有在它有话要说时才开口。
- **嵌套 Terrarium**。 子 Agent 可以透过 `group_add_node`（若它是特权节点）把额外的 Creature 生成到图里。底层基础设施不在乎。
- **纵向包在横向里**。 一个Terrarium中的 Creature本身还会使用子 Agent——混合两种多 Agent轴向。

## 不要被它框住

子 Agent是可选的。对大多数短任务来说，只有工具的 Creature就已经够用。而且既然「子 Agent」在概念上就是「其实现刚好是一整只 Agent 的工具」，两者的界线本来就会模糊：某个工具完全可以在 Python 里派生一个 Agent，而从 LLM 的角度看，这和调用子 Agent没有差别。

## 另见

- [工具](tool.md) ——「它也是一种工具」这个视角。
- [多 Agent概览](../multi-agent/README.md) —— 纵向（子 Agent）与横向（Terrarium）的差异。
- [模式——静默控制器](../patterns.md) —— 输出型子 Agent这个惯用法。
- [子代理指南](../../guides/sub-agents.md) —— 配置内置/内联子代理、预算和运行时插件。
- [reference/builtins.md — Sub-Agents 参考](../../reference/builtins.md) —— 内建子 Agent工具包。
