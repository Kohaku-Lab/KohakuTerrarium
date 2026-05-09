---
title: 动态图
summary: terrarium 的图为何会在运行时变形 —— 连通分量、自动合并 / 自动分裂、图内的「图编辑器」、以及 session 血缘的代价与好处。
tags:
  - concepts
  - multi-agent
  - graph
  - hot-plug
---

# 动态图

## 它是什么

[Terrarium](terrarium.md) 不是固定形状。运行中的 Creature 集合、它们
之间的频道、谁与谁共享一个 session —— 这一切都可以在运行时变化，**不
需要重启**、不会重新建立未受影响的 Creature、也不会丢历史。

引擎把活动系统建模为一张由 Creature 与频道组成的**图**。这张图的每个
连通分量是一个独立的「graph id」，拥有自己的共享 environment 与自己
的 session store。当拓扑变化时，引擎做出反应：

- 加入一只新 Creature → 默认落在一个新的单点分量；若呼叫者明确指定，
  则加入指定分量。
- 一条新接线跨越两个分量 → 它们**自动合并**为一个分量（与一个合并后
  的 session store）。
- Creature 或频道被移除 → 若连通性断开，分量**自动分裂**为新的连通
  片段（session store 复制到每一边）。

这一切都是引擎在收到 mutation 呼叫时确定性地执行的结构性工作。图内
的 Creature 不做这些决定 —— 引擎做。

## 为什么它存在

静态拓扑的多代理运行时无法表达人们真正想在运行时做的事：

- 一个特权节点在任务进行中决定它需要一种没预料到的专家，并想要立刻
  生成一只。
- 两个并排独立运行的 session 在其中一方需要帮助时想合并成一段单一对
  话。
- 一只 Creature 完成它的工作，应该在不影响他人的前提下被收掉；如果
  它原本是连接两半的桥，那两半也应该独立地继续运行。
- 团队应该可以从外部观察，但团队内的人不知道自己被观察；而且观察必
  须在 Creature 来去之间持续追踪它们的身份。

让图变成动态的 —— 并把记账责任交给引擎 —— 让这四件事都不需要在每个
recipe 里特殊处理。

## 心智模型

每一个连通分量是一个**图**。两只 Creature 在同一个图里，当且仅当它们
之间存在一条经由它们共享（监听或送出）的频道的路径。图是以下这些事
情的单位：

- **共享 environment。** 在一个图中宣告的频道活在该图的
  `Environment` 里；只有图中的 Creature 看得见。
- **Session。** 一个 `.kohakutr` 文件撑住一个图。同一个图里的
  Creature 共享历史；不同图的不会。
- **组工具。** 特权操作（生成、移除、频道 CRUD、输出接线 CRUD）作用
  于呼叫者的图。

分量不需宣告。它们是从任意当下的频道相邻关系**推导**出来的。一次连
通性变化就会重新推导。

## 运行时可以变更的东西

| 操作 | 拓扑影响 | Session store 影响 |
|------|----------|---------------------|
| `Terrarium.add_creature` | 新单点分量（默认），或加入指定图 | 当图已挂 store 时绑定 |
| `Terrarium.remove_creature` | 若该 Creature 是桥，可能分裂 | 分裂侧的记账（复制） |
| `Terrarium.add_channel` | 不变更连通性 | 直接无影响 |
| `Terrarium.remove_channel` | 若该频道是唯一路径，可能分裂 | 分裂侧的记账 |
| `Terrarium.connect(a, b, ...)` | 若 `a`、`b` 在不同图 → 合并 | 合并 store；记下 `parent_session_ids` |
| `Terrarium.disconnect(a, b, ...)` | 可能分裂 | 分裂侧的记账 |

同样的 mutation 也透过[组工具](../glossary.md#group-tools--组工具)
（`group_add_node`、`group_remove_node`、`group_start_node`、
`group_stop_node`、`group_channel`、`group_wire`）暴露给图中的
[特权节点](privileged-node.md)。它们合在一起就是图内的**图编辑器**
—— LLM 驱动的特权节点可以靠呼叫工具在执行中演化团队，每一次 mutation
都会发出 `EngineEvent`，让 observer 与运行时提示词保持同步。

## 自动合并

合并发生在跨图 connect 时。引擎会：

1. 在拓扑层联集两张图（creature id、频道宣告、listen / send 边）。
2. 联集两个 `Environment` —— 每一个频道物件从被丢弃的图搬到存活的
   environment，已存在的频道触发器对着存活的 env 重新注入。
3. 把两个 session store 合并为存活图路径下一个新的 store。每一个事
   件从两个旧 store 复制到新 store；新 store 的 meta 记下
   `parent_session_ids` 与 `merged_at` 时间戳。
4. 把每一只受影响的 Creature 的 `graph_id` 重指向存活图。
5. 发出 `TOPOLOGY_CHANGED` 事件，附 `kind="merge"`、`old_graph_ids`、
   `new_graph_ids`、`affected_creatures`。

合并之后，被丢弃的图上原有频道的流量都会经过合并后的 environment 路
由，而 session 写入会指向合并后的 store。

## 自动分裂

分裂发生在一次移除切断了图中两半之间的唯一连通路径时。引擎会：

1. 在 mutation 后的拓扑上计算连通分量。
2. 最大分量保留原 graph id。其他分量铸造新 id。
3. 为每一个新分量分配新的 `Environment`，并把该分量的拓扑频道注册到
   新 env 里。
4. 对每一只受影响的 Creature：把 `creature.graph_id` 指向它的新分
   量、把它的 agent 与 executor 绑到新 env、把它的每一个频道监听触
   发器对着新 env 的频道物件拆掉重注入（让讯息在正确的活动 registry
   上流动）。
5. 把分裂前的 session store 复制到每一个新分量的路径下。每一个子
   store 都继承完整的分裂前历史，meta 记下 `parent_session_ids` 与
   `split_at` 时间戳。
6. 发出 `TOPOLOGY_CHANGED` 事件，附 `kind="split"` 与新的 graph id。

分裂时历史不会遗失 —— 只会被复制。分支 session 从同一起点分歧。

## Resume：recipe 是真理来源

当一份保存的多创造物 session 被 resume 时，引擎会**从 recipe 重建拓
扑**，**不是**用图的冻结快照。session metadata 记着 `config_path`、
`agents`，以及血缘（`parent_session_ids`、`merged_at`、`split_at`）；
引擎播放磁盘上的 recipe 来重建 Creature、频道与接线，再把保存的对话
注入回去。

意思是：

- 在两次执行之间编辑 recipe 是被支持的。新频道会出现、被移除的
  Creature 不见了、输出接线被更新。
- *分裂状态*的快照**不会**被保留。Resume 重建 recipe 自然形状的图；
  若保存当下 session 处于分裂状态，resume 会重建原本合并后的图。
- 血缘 metadata 仍然存活。即使 resume 后的图回到 recipe 的拓扑，你
  仍可以透过 `parent_session_ids` 追踪过去合并 / 分裂到当前 store 的
  历史。

## 特权门槛

不是每一只 Creature 都该能变更图。引擎区分：

- **特权 Creature** —— recipe 的 `root:` 节点、recipe 内 inline 标记
  `privileged: true` 的成员、以及以 `is_privileged=True` 建立的
  Creature。它们持有[组工具](../glossary.md#group-tools--组工具)。
- **工人** —— 由特权呼叫者透过 `group_add_node` 生成的 Creature。它
  们落在呼叫者的图里，但**不**拥有组工具。工人没被引擎显式提权前不
  能再分叉同侪或图边。

特权是运行时 Creature handle 的属性，与底层 agent 配置无关。同一份
配置可以在某个 terrarium 里以特权身份运行、在另一个里以非特权身份运
行。

## 可观测性

每一次拓扑 mutation 都会发出 `EngineEvent`：

- `CREATURE_STARTED` / `CREATURE_STOPPED`
- `OUTPUT_WIRE_ADDED` / `OUTPUT_WIRE_REMOVED`
- `PARENT_LINK_CHANGED`
- `TOPOLOGY_CHANGED`（merge / split / nothing，附带新旧 graph id 与
  受影响的 Creature）
- `SESSION_FORKED` / `CREATURE_SESSION_ATTACHED`

订阅者用 `EventFilter` 在 kind、creature id、graph id、channel 上过
滤。网页 dashboard 用这条流来驱动 live 面板；运行时提示词订阅器用它
来在图变化时刷新受影响 Creature 的 system prompt（这样特权节点的
「图感知」区块永远当前）。

## 不要被它框住

完全没有运行时变化的静态 recipe 是最简单的模式，也是好的预设。当工
作本身是动态的 —— 团队形状在执行中才被发现的开放性研究、一个
session 把另一个拉进来的临时救援、分支与合并并存的并行探索 —— 才会
需要热插拔与组工具创作。

## 另见

- [Terrarium](terrarium.md) —— 图所栖身的运行时引擎。
- [特权节点](privileged-node.md) —— 使用组工具的特权 Creature。
- [impl-notes / graph and sessions](../impl-notes/graph-and-sessions.md)
  —— 合并 / 分裂记账的实际实现方式。
- [reference / builtins — group_* 工具](../../reference/builtins.md)
  —— 组工具表面。
