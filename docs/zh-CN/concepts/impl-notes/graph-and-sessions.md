---
title: 图与 session
summary: terrarium 引擎如何计算连通分量、在 mutation 呼叫下变更拓扑，并在自动合并 / 自动分裂中保持 session store 的一致。
tags:
  - concepts
  - impl-notes
  - graph
  - persistence
---

# 图与 session

## 这个问题要解决什么

一个多创造物 terrarium 是一张可在运行时变化的图。三个特性必须同时成
立：

1. **反应式拓扑。** 运行时的心智模型 —— 谁共享 environment、谁共享
   session —— 必须自动跟随连通性。如果使用者拔掉两半之间最后一条频
   道，那两半应该在不需要操作员介入的前提下变成独立的。
2. **历史不丢。** 合并与分裂是资讯转换；一只 Creature 之前的对话应
   该仍然能从原图的任何后代里复原。
3. **正在飞行的频道讯息不会因为换 environment 而错路由。** 当
   environment 因为合并或分裂而改变时，飞行中的频道触发器必须继续在
   正确的活动 registry 上送达。订阅者不会被通知重新订阅；引擎得让它
   们指向存活的物件。

朴素做法（每次读取都重算图，或在启动时冻结拓扑）会破坏其中之一。

## 考虑过的选择

- **每次 mutation 都即刻全表 reindex。** 每次变更重新算整张图表。简
  单。每次 mutation O(N)，可观测性昂贵（每次变更隐含全 diff），还会
  迫使任何拿着 graph id 的人作废它。
- **懒式：不追踪分量；按需问「A 与 B 连通吗？」** mutation 便宜，但
  共享 environment 的查询昂贵；如果图本身不存在为物件，要把 session
  store 挂到「一个图」就难。
- **mutation 前预测。** 在套用变更前算出 mutation 后的分量，并拒绝
  会造成意外状态的变更。太严格；拓扑变更是使用者动作，不是请求许
  可。
- **套用，再 normalise。** 变更拓扑。每次变更后只在受影响的图内重算
  分量，并发出一个 `TopologyDelta` 描述发生了什么。引擎对 delta 反
  应 —— 重新分配 environment、复制 session store、重指向 Creature、
  重新注入触发器、发事件。我们的做法。

## 我们实际怎么做

### 纯数据图（`terrarium/topology.py`）

拓扑活在一个朴素的 `TopologyState` 值里：

- `state.graphs: dict[graph_id, GraphTopology]` —— 每个连通分量一笔。
  每个 `GraphTopology` 装它的 creature id、频道宣告、二部
  listen / send 边映射。
- `state.creature_to_graph: dict[creature_id, graph_id]` —— 反向索
  引，回答「这只 Creature 在哪个图？」。

Mutation 是纯函数：`add_creature`、`remove_creature`、
`add_channel`、`remove_channel`、`connect`、`disconnect`、
`set_listen`、`set_send`。每个回传一个 `TopologyDelta` 描述发生了什么
（`kind` ∈ {`nothing`, `merge`, `split`}，加 `old_graph_ids`、
`new_graph_ids`、`affected_creatures`）。没有活动 agent，没有 asyncio
—— 整个模组可以纯当资料测试。

### 连通分量（`find_components`、`_normalize_components`）

连通性透过 BFS 在二部图（一边是 Creature、另一边是 channel）上算出。
两只 Creature 同分量，若且唯若它们之间存在一条经由它们共享（监听或
送出）的频道的路径。`find_components` 重建 per-creature ↔ per-channel
邻接映射，并从每一只未访问的 Creature 跑 BFS。

`_normalize_components` 在可能切断连通性的 mutation 之后跑。如果重算
后的分量数 `<= 1`，delta 是 `kind="nothing"`。如果大于 1，最大分量保
留原 graph id，其余分量铸造新 id；频道按它们实际触及的分量重新分配；
delta 报告 `kind="split"` 与受影响的 Creature。

### 合并记账（`channels.connect_creatures`、`_merge_environment_into`、`session_coord.apply_merge`）

当 `Terrarium.connect(a, b, channel=...)` 呼进拓扑层、结果是
`kind="merge"` 时，引擎：

1. 决定存活 graph id 与被丢弃的 graph id。
2. `_merge_environment_into(engine, surviving, dropped)` 把每一个频
   道物件从被丢弃的 environment 搬到存活 environment 的
   `ChannelRegistry`。每一个既存的监听触发器对着新 env 重新注入，让
   它的 `on_send` callback 继续指向正确的 registry 与正确的 session
   store。
3. 把每一只受影响的 Creature 的 `graph_id` 重指向存活图（让后续拓扑
   查询落在正确的位置）。
4. `session_coord.apply_merge(engine, delta)` 整合 session store。它
   呼叫 `merge_session_stores(old_stores, new_path)`，在存活图的路径
   下建立新 store，把每一个事件透过 `copy_events_into` 从每一个旧
   store 复制到新 store（保留 `turn_index`、`spawned_in_turn`、
   `branch_id`，重新打 event id），并把
   `parent_session_ids: [old_a, old_b]` 加上
   `merged_at: <timestamp>` 写入新 store 的 meta。
5. 发出 `TOPOLOGY_CHANGED(kind="merge", ...)`。

新合并 store 立即撑住存活 environment 中每一个频道的持久化 callback
（因为第 2 步重新注入触发器，而 callback 会闭包
`engine.session_store_for(graph_id)` —— 现在解析到合并 store）。

### 分裂记账（`channel_lifecycle.apply_split_bookkeeping`、`session_coord.apply_split`）

当一次移除或断开呼叫回传带 `kind="split"` 的 delta 时，引擎：

1. 为每一个新分量分配新的 `Environment`，并把该分量的拓扑频道注册到
   它的 environment。
2. 对每一只受影响的 Creature：把 `creature.graph_id` 指向它的新分
   量，把它的 agent 与 executor 绑到新 env，把每一个频道监听触发器
   对着新 env 的频道物件拆掉重注入。
3. `session_coord.apply_split(engine, delta)` 呼叫
   `split_session_store(old_store, new_paths)`，在每一个新分量路径下
   建立一个新 store，并 `copy_events_into` 把完整的分裂前历史复制进
   每一个子 store。每一个子 store 在 meta 记下
   `parent_session_ids: [old_graph_id]` 与 `split_at: <timestamp>`。
   session store 反向映射被更新，让每一个新 graph id 解析到自己的
   store。
4. 发出 `TOPOLOGY_CHANGED(kind="split", ...)`。

分裂前的历史在每一边都保留。分支从共同的根分歧。

### 频道持久化 callback（`channels._ensure_channel_persistence`）

当一个频道在挂著 session store 的图的 environment 里被注册时，频道
上会装上一个 `on_send` callback。Callback 透过
`store.save_channel_message()` 把每一次 send 写入 store。Callback 是
幂等的（再次安装会替换自己），且每次呼叫时都会读取频道物件上当下的
`_terrarium_graph_id` —— 因此当频道物件因合并而搬家时，callback 会自
动指向存活 store，无需重装。

这就是合并路径（上面第 2 步）能搬动频道物件而不丢讯息持久化的原因。

### 触发器重注入（`channels.inject_channel_trigger`、`_teardown_existing_trigger`）

`ChannelTrigger` 是订阅在某个特定频道物件上的 async task。在合并或分
裂之后，撑住一只 Creature 监听边的频道物件可能改变身份（不同的
`ChannelRegistry`、不同的 `Environment`）。`inject_channel_trigger` 是
幂等的：它按 id（`channel_{subscriber_id}_{channel_name}`）拆掉任何
既有触发器，从旧频道取消订阅，再对当下活动频道重新订阅。
`apply_split_bookkeeping` 与 `_merge_environment_into` 路径会对每一对
受影响的 Creature × channel 呼叫它。

### Resume 时 recipe 是真理来源（`terrarium/resume.py`、`terrarium/recipe.py`）

`resume_into_engine` **不会**把活动拓扑写到磁盘再播回。它会读
`meta.config_path`（session 建立时记下的 recipe 路径），并在新的引擎
上重新套用 recipe：宣告频道、加入 Creature、接 listen / send 边、注
册输出接线、若 recipe 宣告 root 则呼叫 `assign_root_to`。每一只重建
的 Creature 之上接著由 `session.resume.resume_agent` 注入保存的状态
（对话、scratchpad、触发器）。

一个推论：如果保存当下 session 处于*分裂状态*，resume 会从 recipe 重
建一张图。血缘 metadata（`parent_session_ids`、`merged_at`、
`split_at`）在 resume 后的 store 里仍然存活，因此审计轨迹仍可读，但
活动拓扑会回到 recipe 的自然形状。

## 维持的不变条件

- **连通性 ↔ graph id。** 两只 Creature 在同一个图中，若且唯若它们
  之间透过频道连通。永远如此。
- **一图一 store。** 一个 graph id 至多对应一个 `SessionStore`。跨图
  的 Creature 不共享 store。
- **转换时历史保留。** 合并把历史联集到一个新 store 里；分裂把来源
  store 复制到每一个新 store。没有事件被丢、没有事件变得不可读。
- **血缘被记下。** 每一次转换在新 store 的 meta 写入
  `parent_session_ids` 与 `merged_at` / `split_at` 时间戳。
- **触发器物件永远指向活动频道。** 任何合并或分裂之后，每一个频道监
  听触发器都对着存活的 environment 拆掉重注入。
- **拓扑形状由 recipe 主导。** Resume 时拓扑来自 recipe；session 提
  供 per-creature 状态与血缘 metadata，不提供图结构。

## 它住在程式码哪里

- `src/kohakuterrarium/terrarium/topology.py` —— `TopologyState`、
  `GraphTopology`、`TopologyDelta`、纯 mutation 函数、
  `find_components`、`_normalize_components`、`_merge_graphs`。
- `src/kohakuterrarium/terrarium/engine.py` —— `Terrarium` 编排。
  `add_creature`、`remove_creature`、`connect`、`disconnect`、
  `add_channel`、`remove_channel`，加 environment / session store
  registry。
- `src/kohakuterrarium/terrarium/channels.py` —— `connect_creatures`、
  `_merge_environment_into`、`_ensure_channel_persistence`、
  `inject_channel_trigger`、`_teardown_existing_trigger`。
- `src/kohakuterrarium/terrarium/channel_lifecycle.py` ——
  `apply_split_bookkeeping`、频道移除流程、environment 重新分配。
- `src/kohakuterrarium/terrarium/session_coord.py` —— `apply_merge`、
  `apply_split`、`merge_session_stores`、`split_session_store`、
  `copy_events_into`、meta 刷新辅助函数。
- `src/kohakuterrarium/terrarium/runtime_prompt.py` —— 事件驱动的
  per-creature 提示词刷新，监听 `TOPOLOGY_CHANGED`、
  `CREATURE_STARTED`、`CREATURE_STOPPED`、`OUTPUT_WIRE_ADDED`、
  `OUTPUT_WIRE_REMOVED`、`PARENT_LINK_CHANGED`。
- `src/kohakuterrarium/terrarium/resume.py` —— `resume_into_engine`、
  recipe 驱动的拓扑重建。
- `src/kohakuterrarium/terrarium/events.py` —— `EngineEvent` 分类、
  `EventFilter`。
- `src/kohakuterrarium/session/store.py` —— 协调器使用的
  `SessionStore` API。

## 另见

- [动态图](../multi-agent/dynamic-graph.md) —— 这份实现支撑的使用者
  心智模型。
- [Session 持久化](session-persistence.md) —— 底层的 `.kohakutr` 文
  件格式与 per-creature resume。
- [Terrarium](../multi-agent/terrarium.md) —— 引擎契约。
