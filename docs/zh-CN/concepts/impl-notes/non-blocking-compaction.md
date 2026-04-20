---
title: 非阻塞压缩
summary: 说明当摘要器在背景重建压缩后的对话时，控制器如何持续运作。
tags:
  - concepts
  - impl-notes
  - compaction
---

# 非阻塞压缩

## 这要解决的问题

一个连续执行数小时的 Creature，会不断累积对话内容。最终，
prompt 会超出模型的上下文预算。标准解法是进行压缩：
把较早的轮次摘要成一段精简笔记，近期轮次则保留原始内容。
但压缩本身也是一次 LLM 调用——如果控制器在摘要器工作时
被阻塞，一个常驻型代理就会在重写 50k tokens 的期间冻结
数十秒。

对于偏向程序编写代理风格的 Creature，这或许还能接受；但对于
监控型或对话型Creature，这就是产品缺陷。

## 曾考虑的方案

- **同步暂停**。 停止控制器、做摘要、再恢复。
  很简单，但会造成长时间冻结。
- **交给独立代理**。 对本质上只是
  「把旧轮次改写成一段文字」这件事来说太过头。
- **背景任务 + 原子拼接**。 在控制器运作的同时并行做摘要；
  并在轮次之间替换对话。这就是框架实际采用的做法。

## 我们实际怎么做

对话在概念上会被分成两个区域：

```
  [ ----- 压缩区 ----- ][ --- 即时区（keep_recent_turns）--- ]
  可处理  原始内容，永不摘要
```

流程如下：

1. 每一轮结束后，compact manager 会检查
  `prompt_tokens >= threshold * max_tokens`。
2. 如果成立，就发出 `compact_start` 活动事件，并启动一个
  背景 `asyncio.Task`。
3. 这个任务会：
  - 对压缩区建立快照，
  - 执行摘要用的 LLM（主控制器的 LLM，或是若有配置则使用
  专用且更便宜的 `compact_model`），
  - 产生一份摘要，并原样保留决策、文件路径、错误字串，以及
  其他高讯号 token。
4. 与此同时，控制器会持续处理事件——工具照常执行、
  子 Agent照常生成、用户也可以继续输入。
5. 当摘要完成后，manager 会等待目前轮次结束，然后 **以原子方式** 重写对话：
  - 将旧的压缩区替换为 `{system prompt, 先前摘要,
  新摘要, 即时区原始消息}`，
  - 并发出 `compact_complete` 事件。

## 维持不变的条件

- **不在轮次中途替换**。 对话只会在轮次之间被替换，
  因此控制器在一次 LLM 调用期间，不会看到消息突然消失。
- **压缩期间即时区不会缩小**。 在摘要进行中，新轮次会继续累积到
  即时区；而拼接时会把这点计算进去。
- **摘要会层层累积**。 下一次压缩会产生一份包含前一次摘要的摘要，
  因此历史内容会逐步退化但不会直接遗失。
- **可针对个别Creature停用**。 `compact.enabled: false` 可完全关闭此功能。

## 代码中的位置

- `src/kohakuterrarium/core/compact.py` — 带有
  start/pending/done 状态机的 `CompactManager`。
- `src/kohakuterrarium/core/Agent.py` — `_init_compact_manager()` 会在
  `start()` 时把 manager 接到 Agent 上。
- `src/kohakuterrarium/core/controller.py` — 每轮结束后的 hook，
  会请 manager 评估是否需要压缩。
- `src/kohakuterrarium/builtins/user_commands/compact.py` — 手动触发的
  `/compact`。

## 另请参阅

- [记忆与压缩](../modules/memory-and-compaction.md) — 概念层面的说明。
- [reference/configuration.md — `compact` 参考](../../reference/configuration.md) —
  各Creature可调整的配置项目。
