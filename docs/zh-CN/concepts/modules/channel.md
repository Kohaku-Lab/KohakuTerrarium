---
title: 频道 (Channel)
summary: 具名的消息管道 — queue 与 broadcast — 是多 Agent与跨模块通讯的底层基础。
tags:
  - concepts
  - module
  - channel
  - multi-agent
---

# 频道

## 它是什么 

**频道 (channel)** 是一种带型别的消息管道。一端可以送出；另一端
或多端可以接收。频道存在于Creature的私有会话里，或存在于多只
Creature都看得到的共享 environment 里。

它严格来说不是Creature的「正典」模块之一 — 在 chat-bot → Agent 的
推导路径里，它从来没有出现过。它是让工具与触发器能在多个代理之
间真正变得有用的通讯底层。

## 为什么它存在

当你已经有工具与触发器之后，很自然就会想让两个 Agent 彼此说话。
摩擦最低的做法是：Agent A 的工具写入一则消息；Agent B 有一个触发
器，当某个名字的消息到达时就触发。

这正是频道。它不是什么新点子 — 它只是*命名惯例*加上一点点队列
机制，让「这边写入、那边监听」能成立，而双方都不需要知道彼此是
谁。

## 我们怎么定义它

频道有两种类型：

- **`SubAgentChannel` (queue)** — 消息依 FIFO 排列，每则消息由
  *一个*接收者消费。适合 request/response 或任务派发。
- **`AgentChannel` (broadcast)**— 每个订阅者都有自己的队列，
  每则消息都会送到每个订阅者。适合公告。

频道存在于 `ChannelRegistry` 里。Creature的私有会话有一份 registry；
terrarium 的 environment 有一份共享 registry。Creature可以监听其中任一种。

`ChannelTrigger` 会把频道名称绑到Creature的事件流上 — 每当有消息到达，
就会推入一个 `channel_message` 事件。

## 我们怎么实现它

`core/channel.py` 实现了两种频道类别与 registry。
`modules/trigger/channel.py` 实现了把频道桥接进Creature事件队列的触发器。

自动建立的频道：

- 在 terrarium 里，每个 Creature各有一个 **queue**，名称就是Creature名称
  本身（所以其他Creature可以直接 DM 它）。
- 当存在 root Agent 时，会建立 `report_to_root`。

`ChannelObserver`（`terrarium/observer.py`）可以在频道上挂一个不破坏
性的 callback：observer 能看见每一则送出的消息，但不会消费它们。
这就是 dashboard 能观察那些已经有真实 consumer 在读取的 queue 频道
的方式。

## 因此你可以做什么

- **Terrarium连线**。 terrarium 配置里每一条 listen/send 配置，最终
  都会解析成频道操作。
- **群聊模式**。 `send_message` 工具（任一Creature皆可用）+
  其他Creature上的 `ChannelTrigger` = N 方群聊。不需要新的 primitive。
- **死信 / 失败频道**。 把错误导到专用的 broadcast 频道；一只
  `logger` Creature订阅后写入磁碟。
- **非破坏式除错**。 用 `ChannelObserver` 去偷看一个已有真实 consumer
  持续排空的 queue。
- **跨Creature rendezvous**。 两只同时监听同一个共享频道的 Creature，可以
  轮流处理其中的项目。

## 频道 vs. 输出接线

频道不是Creature彼此沟通的唯一方法。另一个平行机制 —**输出接线
(output wiring)**— 会在每个回合结束时，直接把一个
`creature_output` `TriggerEvent` 发送到目标Creature的事件队列里，双方都
不需要调用 `send_message`。该用哪一种：

- **频道**— 条件式路由（approve 或 revise）、群聊、状态广播、
  延后 / 非必然流量、观察。由Creature自己决定要不要送、送去哪里。
- **输出接线** — 确定性的 pipeline 边（「runner 的输出永远送给
  analyzer」）。以宣告式配置，并在回合结束时自动触发。

同一个 terrarium 可以自由混用两者。见
[terrarium](../multi-agent/terrarium.md) 与
[guides/terrariums 指南](../../guides/terrariums.md#output-wiring)。

## 不要被它框住

独立运作的 Creature其实不需要频道 — 它的工具不会 `send_message`，
它的触发器也不会监听。频道不是推导里的一等模块；它是一种惯例，
只是因为太多 multi-agent 使用情境最后都能化约成它，所以框架乾脆
把它提供成 primitive。

这是「框架会自己弯折自己的抽象」最清楚的例子。频道活在六模块分
类之外，而把「Agent A 告诉 Agent B 某件事」实现成「工具写入、
触发器触发」本来就是刻意混用不同层。见 [boundaries](../boundaries.md)。

## 另见

- [工具](tool.md) — 传送端那一半。
- [触发器](trigger.md) — 接收端那一半。
- [多 Agent / terrarium](../multi-agent/terrarium.md) — 频道在那里真正亮起来成为连线。
- [模式](../patterns.md) — 群聊、死信、observer。
