---
title: 多 Agent 系统
summary: 两个轴 — 纵向 (子 Agent) 与横向 (Terrarium + 频道 + 输出接线) — 何时挑哪一个。
tags:
  - concepts
  - multi-agent
  - overview
---

# 多 Agent 系统

KohakuTerrarium 里有两个不同的多 Agent轴向，它们解决不同的问题。在伸手拿Terrarium之前，先确认你真的需要的是哪一种。

## 纵向 (单体式)

```
  主Creature
  /  |  \
  子 Agent  子 Agent  子 Agent
  (规划)  (实现)  (审查)
```

一只主Creature派遣多个子 Agent。每个子 Agent有自己的上下文、自己的提示词。结果：用户看到一份对话，父代理背后藏了许多专家对话。

Claude Code、OpenClaw、Oh-My-Opencode 以及大多数现代的 coding Agent 都属于这一类。

- **什么时候用**： 任务可以很自然地拆成若干阶段，而且你想把上下文隔离开。
- **KT 给你的东西**： 子 Agent是原生功能。在 `sub-agents[]` 里配置，用名字调用。参见 [子 Agent](../modules/sub-agent.md)。

## 横向 (模块式)

```
  +-- Creature_a -------+  +-- Creature_b -------+
  |  (某个专家)  | <==> |  (另一个专家)  |
  +-----------------+  +-----------------+
  共享频道 + 运行时
```

数个独立的专家Creature并排执行，各自有自己的设计。它们透过频道对话。

CrewAI、AutoGen、MetaGPT 瞄准的就是这个轴。

- **什么时候用**： 任务符合明确的多角色工作流，而且这些角色真的是不同的 Agent (不同提示词、工具、模型)，不只是一只 Agent 的不同子任务。
- **KT 给你的东西**：[Terrarium](terrarium.md)。Terrarium 是托管 Creature、拥有频道图、并在图变化时处理拓扑 + session 记账的运行时引擎。它自己不执行 LLM —— 推理活在它内部的 Creature 里。

## 经验法则 **先试纵向**。 大部分「我需要多 Agent」的直觉其实是「我需要上下文隔离」或「我需要一个专家提示词」，两者都可以用子 Agent解决。

当你真的想要 **不同的 Creature** 一起合作、且工作流稳定到可以用拓扑表达时，再拿出Terrarium。

## 关于定位

我们把Terrarium定位为横向多 Agent的 **一种提议架构。** 各块拼图今天都可以组在一起用：频道处理选用/条件性的流量 **，输出接线 (output wiring)** 处理确定性的 pipeline 边 (框架层级的配置，会自动把Creature回合结束的文字推进目标的事件队列 — 不用 `send_message`)，再加上热插拔、观察、以及对 root 的生命周期通报。kt-biome 的 `auto_research`、`deep_research`、`swe_team`、`pair_programming` Terrarium把这些东西完整走过一遍。

我们还在摸索的是 **习惯**：什么时候该用连线、什么时候该用频道；条件分支不手刻频道拼装怎么表达；怎么让 UI 对连线活动的显示和频道流量一样清楚。这些没结论的问题放在 [ROADMAP](../../../ROADMAP.md)。

当你真的要让不同Creature合作时用Terrarium。当任务在一个 Creature内部可以自然拆解时用子 Agent — 对多数「我需要上下文隔离」的直觉而言，纵向比较简单。框架不替你挑，你自己看情况。

## 这一节有什么

- [Terrarium](terrarium.md) — 横向多创造物图的运行时引擎。
- [特权节点](privileged-node.md) — 图中持有组工具的 Creature；`root:` 配方关键字会把一个节点提升为特权。
- [动态图](dynamic-graph.md) — 自动合并 / 自动分裂、图编辑器，以及 recipe 在 resume 时是真理来源的设计。

## 延伸阅读

- [子 Agent](../modules/sub-agent.md) — 纵向的原语。
- [频道](../modules/channel.md) — Terrarium与某些子 Agent模式共享的底层机制。
