---
title: 组合一个 Agent
summary: 六个 Creature 模块如何在运行时透过单一 TriggerEvent 信封互动。
tags:
  - concepts
  - foundations
  - runtime
---

# 组合一个 Agent

[什么是 Agent](what-is-an-agent.md) 介绍了六个模块。本页要说明它们实际上如何拼成一只正在运作的 Creature。

## 单一信封：`TriggerEvent`

所有来自控制器外部的东西，都会以 `TriggerEvent` 的形式进来：

- 用户输入文字 → `TriggerEvent(type="user_input", content=...)`
- 计时器触发 → `TriggerEvent(type="timer",...)`
- 工具执行完成 → `TriggerEvent(type="tool_complete", job_id=..., content=...)`
- 子 Agent 返回 → `TriggerEvent(type="sub-agent_output",...)`
- 频道消息 → `TriggerEvent(type="channel_message",...)`
- 上下文注入 → `TriggerEvent(type="context_update",...)`
- 错误 → `TriggerEvent(type="error", stackable=False,...)`

所有事情共享一个信封。控制器不需要为每一种来源各写一条不同的程序路径；它只要问：「我这一轮拿到了哪些事件？」这就是整个架构上的简化。

## 事件队列

```
  +-----------+  +---------+  +-----------+  +----------+
  | input.get |  | trigger |  | tool done |  | sub done |
  +-----+-----+  +----+----+  +-----+-----+  +-----+----+
  \  \  /  /
  \  \  /  /
  +------------ event queue ------------+
  |
  v
  +------------+
  | Controller |
  +------------+
```

每一个唤醒来源都会把事件推进同一个队列。多个「同时」到达的事件可以是 **stackable** 的——控制器会把它们合并成同一轮的用户消息，因此一波活动高峰不会直接变成一波 LLM 调用高峰。

不可堆叠的事件（错误、优先讯号）会打断这个批次。它们会在自己的轮次里单独处理。

## 一轮的流程，逐步拆开

```
  +---- collect events from queue (batch stackable)
  |
  |  +- build turn context (job status + event content, multimodal-aware)
  |
  |  +- call LLM in streaming mode
  |
  |  during stream:
  |  - text chunks -> output
  |  - tool blocks detected -> asyncio.create_task(run tool)
  |  - sub-agent blocks detected -> asyncio.create_task(run sub)
  |  - framework commands (info, jobs, wait) -> inline
  |
  |  +- await direct-mode tools + sub-agents
  |
  |  +- feed their results back as new events
  |
  |  +- decide: loop or break
  +---- back to event queue
```

有几个值得注意的不变条件：

1.**工具会立刻开始**。 工具区块一解析完成——远在 LLM 还没说完之前——我们就会把它派发成一个新 task。同一轮里的多个工具会平行执行。详见 [impl-notes/stream-parser](../impl-notes/stream-parser.md)。
2.**同一时间只会有一轮 LLM**。 每个 Creature各自有一把 lock，保证控制器不会被重入。触发器可以自由触发，但它们只会进队列。
3.**direct / background / stateful** 是派发模式，不是三套分离系统。参见 [modules/tool](../modules/tool.md)。

## 其他模块放在哪里

- **输入 (Input)** 会把事件推进队列；除此之外它本身没有变。
- **触发器 (Trigger)** 各自拥有一个背景 task，当条件成立时就把事件推进队列。
- **工具与子 Agent** 透过 executor / sub-agent manager 执行。它们完成后会变成新的事件——回圈就这样闭合。
- **输出 (Output)** 消费控制器产生的文字与工具活动串流，并把它送往一个或多个 sink（stdout、TTS、Discord，或任何你配置的目的地）。

## 在这个层级，概念文件有涵盖与没涵盖什么

本页是架构总览。每个模块更深入的故事，都在各自的模块文件里：

- [Controller](../modules/controller.md) — 回圈本身
- [Input](../modules/input.md) — 第一个触发器
- [Trigger](../modules/trigger.md) — 从世界到 Agent 的唤醒
- [Output](../modules/output.md) — 从 Agent 到世界
- [Tool](../modules/tool.md) — Agent 的手
- [Sub-Agent](../modules/sub-agent.md) — 受上下文范围限制的委派者

另外有两个横切性的部分，适合放在独立章节，而不是压在某一个模块上：

- [Channel](../modules/channel.md) — 工具、触发器与 terrarium 共同分享的通讯基底。
- [Session and environment](../modules/session-and-environment.md) — 私有状态与共享状态的切分。

## 延伸阅读

- [Agent as a Python object](../python-native/agent-as-python-object.md) — 这张图在嵌入式使用时，如何映射回一般 Python。
- [impl-notes/stream-parser](../impl-notes/stream-parser.md) — 为什么工具会在 LLM 停止之前就开始执行。
- [impl-notes/prompt-aggregation](../impl-notes/prompt-aggregation.md) — 驱动这个回圈的 system prompt 是怎么建出来的。
