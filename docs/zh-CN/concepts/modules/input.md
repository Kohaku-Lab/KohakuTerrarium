---
title: 输入 (Input)
summary: 将用户消息带进事件队列的特化触发器。
tags:
  - concepts
  - module
  - input
---

# 输入

## 它是什么

**输入 (input)** 模块是外部世界把工作交给Creature的方式。在正典推导中，
它位于控制器之前，负责触发第一个事件。实务上，它只是一种特定型态
的触发器 — 依惯例被标记为「用户输入」的那一种。

## 为什么它存在

如果一个 Creature只能响应环境中的触发器（timer、channel、webhook），
那你就没办法和它聊天。大多数 Agent 至少有时会在人类参与的回圈中运作，
而那个人类需要一个可以输入文字的地方。

## 我们怎么定义它

`InputModule` 实现一个非同步方法 `get_input()`，它会阻塞直到某个
`TriggerEvent` 准备好。它返回的任何东西，都会像 timer 触发或 channel
消息一样，被推进事件队列。

这也是为什么文件一直说「input 也是 trigger」— 从结构上来看确实如此。
两者的差异主要在生命周期（input 通常在前景，trigger 通常在背景）
以及意图（input 承载的是用户内容）。

## 我们怎么实现它

内建输入模块：

- **`cli`**— 由 `prompt_toolkit` 驱动的行编辑器。支持历史纪录、
  slash commands、多行输入与粘贴。
- **`tui`**— 当Creature在 Textual 下执行时，TUI composer 就是输入来源。
- **`whisper`**— 本机麦克风 + Silero VAD + OpenAI Whisper；会把
  ASR 事件以 `user_input` 形式送出。
- **`asr`**— 自订语音辨识模块的抽象基底。
- **`none`**— 永远不产生事件的 stub；给纯 trigger 驱动的 Creature使用。

自订输入可透过Creature配置中的 `type: custom` 或 `type: package`
注册。它们必须实现 `InputModule`，并由 `bootstrap/io.py` 加载。

## 因此你可以做什么

- **纯 trigger Creature**。 `input: { type: none }` 加上一个或多个
  trigger：cron Creature、channel watcher、webhook receiver。
- **多介面聊天**。 由 HTTP 驱动的部署不需要 CLI 输入 —
  `AgentSession` transport 可以透过 `inject_input()` 以编程方式推送
  用户内容。
- **感测器式输入**。 接上文件系统监控器、Discord listener，或 MQTT
  consumer。Creature本身不会知道差别。
- **把输入当成策略层**。 输入模块可以在内容抵达控制器之前先转换
  用户输入 — 翻译语言、做 moderation 检查、移除秘密资讯。

## 不要被它框住

输入是可选的。没有「人类坐在终端机前」的 Discord bot Creature，可以完全
省略 input，改由 HTTP WebSocket trigger 驱动自己。反过来说，一个 Creature
也可以同时有多个有效输入介面 — 用户能在 CLI 打字，同时 webhook 在推
事件，timer 也能一起触发。

## 另见

- [触发器](trigger.md) — 一般情况；input 只是它的特定形状。
- [reference/builtins.md — Inputs 参考](../../reference/builtins.md) — 内建输入模块完整列表。
- [guides/custom-modules.md 指南](../../guides/custom-modules.md) — 如何写你自己的输入。
