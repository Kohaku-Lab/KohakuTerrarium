---
title: 特权节点
summary: 图中已注册组工具的生物 —— `root:` 配方关键字会把一个节点提升为该状态。
tags:
  - concepts
  - multi-agent
  - privileged
  - root
---

# 特权节点

## 它是什么

**特权节点**（privileged node）是位于[图](../glossary.md#graph)中的一只
生物，被授予了变更所属图所需的[组工具](../glossary.md#group-tools)：生
成或移除其他生物、绘制或删除频道、启动或停止成员、查询图的状态。从结
构上说，它就只是另一只生物 —— 同样的配置、同样的模块、同样的生命周
期。让它「特权」的，是运行时的旗标（`creature.is_privileged = True`）以
及引擎在提升时一并执行的工具注册。

`terrarium` 配方里的 `root:` 是把一个节点标记为特权的其中一种方式。配
方也可以在成员上内联标记特权；引擎 API 接受在生成生物时传入
`privileged=True`。透过工具生成的工人生物（经由 `group_add_node`）默认
**不是** 特权 —— 工人没有被显式提权前不能再分叉同侪。

## 为什么它存在

两个需求共享同一个答案：

1. **能够编辑自己的图。** 多代理工作经常在执行中才发现真正需要的团队
   形状。某个节点必须被允许呼叫 `group_add_node`、`group_channel` 等
   工具。这个旗标就是用来标识「哪一个」。
2. **面向用户的接口。** 当人类在与图互动时，需要一个单一的对话对象。
   那个节点通常也想要同样的权力 —— 看到正在发生什么、生成助手、重新
   接线频道 —— 所以面向用户的节点和特权节点常常是同一只。

`root:` 配方关键字把第二种情况收为一行简写：宣告一个特权节点，并套上
标准的「面向用户 root」接线（一条 `report_to_root` 频道供所有人汇报、
root 监听图中其他每一条频道）。底层机制就是特权旗标加上接线；「root」
只是惯例。

## 我们怎么定义它

让节点变成特权的三种方式：

### 1. `root:` 配方关键字

```yaml
terrarium:
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md     # 团队专用的委派提示词
    controller:
      reasoning_effort: high
  creatures:
    - ...
```

配方加载器会建立该节点、把它标记为特权、开启（或重用）一条
`report_to_root` 频道、把图中其他每一只生物都接线为可送往该频道、让该
节点监听图中其他每一条频道，并强制注册组工具。它同时被挂载为面向用户
的接口（TUI / CLI / 网页 tab）。

### 2. 在配方成员上内联 `privileged: true`

```yaml
terrarium:
  creatures:
    - name: planner
      base_config: "@kt-biome/creatures/general"
      privileged: true
      ...
```

适用于「我要一个不是面向用户的特权成员」—— 例如，旁边坐着几位工人的
特权「主管」节点，独立于另一个面向用户的 root。

### 3. 程序化提权

```python
async with Terrarium() as engine:
    sup = await engine.add_creature(
        "@kt-biome/creatures/general",
        is_privileged=True,
    )
    # sup 立即拥有组工具

# 或者，要套上完整的 root 风格接线（report_to_root + 全监听）：
from kohakuterrarium.terrarium.root import assign_root_to
assign_root_to(engine, sup)
```

`engine.add_creature(..., is_privileged=True)` 是最小提权：旗标被设
上、`force_register_privileged_tools` 被执行。`assign_root_to(engine,
creature)` 是完整的 root 风格助手 —— 特权 + `report_to_root` 频道 + 全
监听接线。

## 我们怎么实现它

- **特权旗标：** `Creature.is_privileged` —— 这是生物句柄的运行时属
  性，与底层 agent 配置无关。
- **工具注册：** `terrarium/tools_group.py` 暴露
  `force_register_basic_tools`（每只生物都有）与
  `force_register_privileged_tools`（仅在特权节点上）。特权工具表面是
  `group_add_node`、`group_remove_node`、`group_start_node`、
  `group_stop_node`、`group_channel`、`group_wire`、`group_status`。
- **配方 `root:`：** 配方加载器在节点建立后呼叫 `assign_root_to`。
  `terrarium/root.py:assign_root_to` 会确保 `report_to_root` 频道存
  在、把图中其他每一只生物接线为可送往该频道、让特权节点监听每一条已
  存在的频道、把它标记为特权，并注册特权工具表面。
- **拓扑刷新：** 运行时提示订阅器会监听 `TOPOLOGY_CHANGED` 事件，并为
  每一只受影响的生物重新生成「图感知」区块 —— 因此特权节点的提示词永
  远反映当前的生物、频道与接线。

## 因此你可以做什么

- **面向用户的指挥者。** 用户对特权节点说：「叫 SWE 修 auth bug，再叫
  reviewer 批准。」节点透过频道送讯息，并监看 `report_to_root` 得知完
  成情况。
- **动态团队建构。** 特权节点呼叫 `group_add_node` 生成专家、
  `group_channel` 宣告频道、`group_wire` 加入输出接线、
  `group_remove_node` / `group_stop_node` 收掉成员。
- **跨图重接线。** `group_channel` 若指向呼叫者图之外的目标，会经过
  `Terrarium.connect` 路由 —— 两个图（与它们的 session store）会合并，
  呼叫者就能接管原本独立的生物。
- **每个图可以有多个特权节点。** 没有规定只能有一个。一个图可以同时有
  面向用户的 root 和特权主管，或多位主管分摊团队。
- **可观测性的枢纽。** root 风格的特权节点会自动监听每一条频道并接收
  `report_to_root` 的流量 —— 这正是执行摘要插件、告警规则等工作的最
  佳位置。

## 不要被它框住

完全没有特权节点的图也合理 —— 像是无头 pipeline、cron 驱动的协调、批
次作业。特权只是为了运行时编辑而设的便利；如果你的团队形状由配方固定
下来，可能根本用不到它。

## 另见

- [Terrarium](terrarium.md) —— 图与其特权节点所栖身的引擎。
- [动态图](dynamic-graph.md) —— 组工具如何变更拓扑、引擎如何反应。
- [多代理概览](README.md) —— 特权节点在整个模型中的位置。
- [reference/builtins.md — group_* 工具](../../reference/builtins.md)
  —— 特权工具表面。
