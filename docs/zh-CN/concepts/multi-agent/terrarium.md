---
title: Terrarium
summary: 横向连线层——频道处理选用流量、输出接线处理确定性边，再叠上热插拔与观察。
tags:
  - concepts
  - multi-agent
  - terrarium
---

# Terrarium

## 它是什么

**Terrarium (terrarium)** 是一个把多只Creature一起执行的纯连线层。它自己没有 LLM、没有智慧，也不做决策。它只做两件事：

1. 它是一个管理Creature生命周期的 **运行时**。
2. 它拥有一组Creature之间可用来互相对话的 **共享频道**。

这就是它全部的契约。

```
  +---------+  +---------------------------+
  |  User  |<----->|  Root Agent  |
  +---------+  |  (terrarium tools, TUI)  |
  +---------------------------+
  |  ^
  sends tasks  |  |  observes
  v  |
  +---------------------------+
  |  Terrarium Layer  |
  |  (pure wiring, no LLM)  |
  +-------+----------+--------+
  |  swe  | reviewer |....  |
  +-------+----------+--------+
```

## 为什么它存在

当Creature变得可携——一个 Creature能单独执行，同一份配置也能独立运作——你就需要一种方法把它们组合起来，同时又不强迫它们彼此知道对方的存在。Terrarium就是这个方法。

它维持的核心不变条件是：Creature永远不知道自己在Terrarium里。它只知道要监听哪些频道名称、往哪些频道名称送消息，就这样而已。把它从Terrarium拿出来，它仍然可以作为独立 Creature执行。

## 我们怎么定义它

Terrarium配置：

```yaml
terrarium:
  name: my-team
  root:  # 可选；位于团队外、面向用户的 Agent
  base_config: "@pkg/creatures/general"
  system_prompt_file: prompts/root.md  # 团队专用的委派提示词
  creatures:
  - name: swe
  base_config: "@pkg/creatures/swe"
  output_wiring: [reviewer]  # 确定性边 → reviewer
  channels:
  listen:  [tasks, feedback]
  can_send:  [status]
  - name: reviewer
  base_config: "@pkg/creatures/swe"  # reviewer 角色来自 prompt，而不是专用Creature
  system_prompt_file: prompts/reviewer.md
  channels:
  listen:  [status]
  can_send:  [feedback, status]  # 条件式：approve vs. revise 仍走频道
  channels:
  tasks:  { type: queue }
  feedback: { type: queue }
  status:  { type: broadcast }
```

运行时会自动为每个 Creature建立一个队列（名称就是它自己的名字，方便其他成员私讯它），而如果存在 root，还会建立一个 `report_to_root` 频道。

## 我们怎么实现它

- `terrarium/runtime.py` —— `TerrariumRuntime` 以固定顺序协调启动（建立共享频道 → 建立Creature → 接好 triggers → 最后建立 root，但先不启动）。
- `terrarium/factory.py` —— `build_creature` 加载Creature配置（支持 `@pkg/...` 解析），用共享 environment + 私有 session 建立 `Agent`，为每个 listen 频道注册一个 `ChannelTrigger`，并在 system prompt 中注入一段频道拓扑说明。
- `terrarium/hotplug.py` —— 运行时的 `add_creature`、`remove_creature`、`add_channel`、`remove_channel`。
- `terrarium/observer.py` —— 用于非破坏式监控的 `ChannelObserver`（让 dashboard 可以旁观而不消耗消息）。
- `terrarium/api.py` —— `TerrariumAPI` 是程序介面的 fa?ade；内建的Terrarium管理工具（`terrarium_create`、`creature_start`、`terrarium_send`、…）都透过它路由。

## 因此你可以做什么

- **明确分工的专家团队**。 两只 `swe` Creature透过 `tasks` / `review` / `feedback` 频道拓扑协作，而 reviewer 角色则由 prompt 驱动。
- **面向用户的 root Agent**。 见 [root-Agent](root-agent.md)。它让用户只和一只 Agent 对话，再由那只 Agent 去编排整个团队。
- **透过输出接线建立确定性的 pipeline 边**。 在Creature配置里宣告它的回合结束输出要自动流向下一阶段——不需要依赖 LLM 记得调用 `send_message`。
- **热插拔专家**。 不需重启，就能在会话中途加入新Creature；现有频道会直接接上。
- **非破坏式监控**。 挂上一个 `ChannelObserver`，就能看见 queue 频道中的每则消息，而不会和真正的 consumer 抢消息。

## 与频道并存的输出接线

频道是原本的答案，而且现在仍然是正确答案，适合处理 **条件性与选用流量**：会批准*或*要求修改的 critic、任何人都可读的状态广播、群聊式侧通道。这些都依赖Creature自己调用 `send_message`。

输出接线则是另一条框架层级的路径：Creature在配置里宣告 `output_wiring`，运行时就会在回合结束时，把 `creature_output` TriggerEvent 直接送进目标的事件队列。没有频道、没有工具调用——这个事件走的是和其他 trigger 相同的路径。

把连线用在 **确定性的 pipeline 边**（「下一步一定要交给 runner」）。把频道留给连线无法表达的条件式 / 广播 / 观察情境。两者可以在同一个Terrarium里自然组合——kt-biome 的 `auto_research` 与 `deep_research` Terrarium正是这样做的。

连线的配置形状与混合模式，请见 [Terrarium指南](../../guides/terrariums.md#output-wiring)。

## 说实话，我们的定位

我们把Terrarium视为横向多 Agent的 **一种提议架构**，而不是已经完全定案的唯一答案。各个部件今天已经可以一起工作（连线 + 频道 + 热插拔 + 观察 + 对 root 的生命周期回报），而且 kt-biome 的Terrarium也把这整套从头到尾跑通了。我们仍在学习的是惯用法：什么时候该优先用连线、什么时候该用频道；要怎么在不手刻频道 plumbing 的前提下表达条件分支；要怎么让 UI 对连线活动的呈现能和频道流量并列。

当工作流本质上就是多Creature协作，而且你希望Creature保持可携时，就用它。当任务比较自然地在一个 Creature内部拆解时，就用子 Agent（纵向）——对多数「我需要上下文隔离」的直觉来说，纵向通常更简单。两种都合理；框架不替你做决定。

至于我们正在探索的完整改进方向（UI 中连线事件的呈现、条件式连线、内容模式、连线热插拔），请参见 [ROADMAP](../../../ROADMAP.md)。

## 不要被它框住

没有 root 的Terrarium是合理的（无头协作工作）。没有Creature的 root，则是一只附带特殊工具的独立 Agent。一个 Creature在不同执行中，可以属于零个、一个或多个Terrarium——Terrarium不会污染Creature本身。

## 另见

- [多 Agent概览](README.md) —— 纵向与横向。
- [Root 代理](root-agent.md) —— 位于团队外、面向用户的 Creature。
- [频道](../modules/channel.md) —— Terrarium所由之构成的原语。
- [ROADMAP](../../../ROADMAP.md) —— Terrarium接下来的方向。
