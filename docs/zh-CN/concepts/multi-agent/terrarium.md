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

**Terrarium (terrarium)** 是托管行程内所有运行中Creature的运行时引擎。它自己没有 LLM、没有智慧，也不做决策。每个进程一个引擎；同一个引擎里可以共存多个互不相连的 graph。

独立 Agent 是引擎里的 **1-creature graph**。多 Agent 团队则是用频道连起来的 **connected graph**。你以前称为「terrarium」的设定档，现在是一份 **recipe** — 「加这些 Creature、宣告这些频道、接这些边」的序列，由引擎执行。引擎本身始终存在；recipe 只是把它填满。

引擎做这些事：

1. **Creature CRUD** — 加、移除、列出、检视。
2. **Channel CRUD** — 宣告、连接 Creature、断开。
3. **输出接线** — 回合结束的事件送进指定目标。
4. **生命周期** — start、stop、shutdown。
5. **Session 合并 / 分裂**，跟著拓扑变更走。
6. **可观测性** — 一切可观察的事都进 `EngineEvent` stream。

这就是它全部的契约。

### 心智模型：单一团队、单一 Root

入门时——多数使用者后续也维持这个画面——你会从一支团队加上一只面向使用者的 Root Creature 看起：

```
  +---------+       +---------------------------+
  |  User   |<----->|        Root Agent         |
  +---------+       |  (terrarium tools, TUI)   |
                    +---------------------------+
                          |               ^
            sends tasks   |               |  observes
                          v               |
                    +---------------------------+
                    |     Terrarium Layer       |
                    |   (pure wiring, no LLM)   |
                    +-------+----------+--------+
                    |  swe  | reviewer |  ....  |
                    +-------+----------+--------+
```

这是 **per-graph 视角**：上方一只 Root，下方一张连通的同侪图，中间是「terrarium 即接线」。这是框架原生提供、也是大多数 recipe 编码的样子。如果你只需要这些，看到这就够了——本节剩下的是当你超出单一团队画面后才需要的引擎细节。

### 引擎全貌：执行所有 graph 的 runtime

引擎是行程层级的 host。一个行程一个引擎。引擎里可以同时存在任意多张 graph——你的团队、你临时拉起来快速对话的独立 Agent、没有同侪的监控 Creature——每张都是独立的 connected component。拓扑不是冻结的；channel 可以在 runtime 跨 graph 拉起来（造成 graph 合并），也可以拆掉（可能让 graph 分裂回去）。

```
              +-----------------------------------------+
              |              Terrarium engine           |
              |          (one per process, no LLM)      |
              +-----------------------------------------+
                |                  |                |
         graph A             graph B           graph C
   +-------------------+ +-------------+   +-------------+
   | root <- swe       | | scout       |   | watcher     |
   |    \-> reviewer   | | (solo)      |   | (no peers)  |
   |    \-> tester     | +-------------+   +-------------+
   +-------------------+

         |  ^
         |  |  connect(scout, swe, channel="leads")
         v  |  -> graph A 和 B 合并；environments 联集，
            |     已 attach 的 session store 也合并。
            |
         |  |  disconnect(reviewer, tester, ...)
         v  |  -> 若移除连结让 graph 断开，A 会分裂；
            |     每一边都拿到 parent session 的副本。
```

每一件可观察的事——文字片段、工具活动、channel 讯息、拓扑变更——都从同一个事件汇流排（`EngineEvent` + `EventFilter`）流出。无论你有一张 graph 或十二张，都用一个 filter 订阅。上面那张 per-graph 的心智模型只是这个引擎的一种 **投影**。

超出单一团队场景之后，这个层次给你的：

- **一个行程多个 session** — 服务端可以并排 host 上百个使用者 session，不需要每张 graph 都拉一个独立的运行时进程。
- **runtime 跨 graph 重接线** — 在两个独立 run 之间画一条 channel 就能合并它们；session 历史会自动合并。
- **统一可观测性** — 一个订阅 filter 涵盖所有事件。
- **保留分层无知** — Creature 仍然不知道自己在引擎里。它只知道自己的 agent、工具，和它所在 graph 注入的 channel handle。

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

- `terrarium/engine.py` —— `Terrarium` 类。每个进程一个。拥有拓扑状态、live Creature、environment、挂著的 session store、订阅者列表。是 async context manager (`async with Terrarium() as t:`)，并附 classmethod factory (`from_recipe`、`with_creature`、`resume` — 最后一个尚未实现)。
- `terrarium/topology.py` —— 纯数据 graph 模型 (`TopologyState`、`GraphTopology`、`ChannelKind`、`TopologyDelta`)。没有 live agent 引用；不需 asyncio 就能测。引擎在它上面叠 live state。
- `terrarium/creature_host.py` —— `Creature`，引擎对每只 Creature 的 wrapper。把以前独立 Agent 与频道感知的两个面合成同一个型别。
- `terrarium/recipe.py` —— 走完一份 `TerrariumConfig` 套到引擎上：宣告频道、为每只 Creature 加一条 direct channel、若有 root 加 `report_to_root`、接 listen / send 边、注入频道触发器、启动一切。
- `terrarium/channels.py` —— 频道注入 (当一只 Creature 加入了一个有它要 listen 的频道的 graph，引擎会往它的 agent 加一个 `ChannelTrigger`)，以及 `connect_creatures` / `disconnect_creatures` 的本体。
- `terrarium/root.py` —— `assign_root` 辅助函数。给一只已经在 graph 里的 Creature，把它指定为该 graph 的 Root：宣告（或重用）一个 `report_to_root` 频道、把 graph 内每只其它 Creature 接成在该频道上送讯息、让 Root 在每个既有频道上 listen，并把 `creature.is_root = True` 翻起来。纯粹是 channel + wiring；工具注册和使用者 IO 挂接留给上层处理。当你以 imperative 方式建 graph 又想要传统「一团队一 Root」拓扑而不走 recipe 档案时使用。
- `terrarium/session_coord.py` —— Session 合并 / 分裂策略。Graph 合并时把两边旧 store 合成一份新的；Graph 分裂时把 parent store 复制到两边。
- `terrarium/events.py` —— `EngineEvent` 分类，加 `EventFilter`、`ConnectionResult`、`DisconnectionResult`。

新代码请直接用 `Terrarium`。顶层 re-export 是稳定的：`from kohakuterrarium import Terrarium, Creature, EngineEvent, EventFilter`。

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
