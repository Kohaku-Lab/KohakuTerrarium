---
title: 输出 (Output)
summary: Creature如何对外说话 — 将文字、活动与结构化事件扇出到各个 sink 的输出路由器。
tags:
  - concepts
  - module
  - output
---

# 输出

## 它是什么

**输出 (output)** 模块是Creature响应其世界的方式。它接收控制器送出的所有内容 —
来自 LLM 的文字 chunk、工具开始 / 完成事件、活动通知、token 使用量更新 —
并把每一种内容路由到正确的 sink。

sink 可以不只一个。Creature可以同时输出到 stdout、串流到 TTS、推送到 Discord，
并且写入文件。

## 为什么它存在

「把 LLM 回复打印到 stdout」只是最简单的情况。真实部署还得回答一些简单情况
不会碰到的问题：

- 当有三个 listener 时，串流中的 LLM chunk 要送去哪里？
- 工具活动要走同一条流，还是另一条？
- 面向用户的文字与面向日志的文字，应不应该共享同一个 sink？
- 如果Creature跑在 web UI 里，究竟是谁在订阅这些事件？

框架不想替每种介面各自特判，所以提供一个统一的 router，把每个 sink 都视为
具名输出。

## 我们怎么定义它

`OutputModule` 是一个非同步 consumer，具有像是
`on_text(chunk)`、`on_tool_start(...)`、`on_tool_complete(...)`、
`on_resume(events)`、`start()`、`stop()` 等方法。`OutputRouter`
持有一组这类模块 — 一个预设输出，以及任意数量的 `named_outputs` —
并把事件扇出出去。

`controller_direct: true`（预设值）表示控制器的文字串流会直接流向预设输出。
`controller_direct: false` 则允许你在中间插入处理器（rewriter、安全过滤器、
摘要器）。

## 我们怎么实现它

内建输出：

- **`stdout`**— 一般终端机输出，可配置 prefix / suffix / stream-suffix。
- **`tts`**— 文字转语音；后端包含 Fish、Edge、OpenAI，执行时自动选择。
- **`tui`**— 当Creature在 TUI 下执行时，使用 Textual 显示。
- **（隐含）web streaming output**— 当Creature跑在 HTTP/WebSocket server
  里时使用。

`OutputRouter`（`modules/output/router.py`）也提供一条 activity stream，
供 TUI 与 HTTP client 显示工具开始 / 完成事件，而不必把它们混进文字通道。

## 因此你可以做什么

- **安静的控制器，串流的子 Agent**。 把子 Agent标记为 `output_to: external` —
  它的文字会直接串流给用户，而父控制器则维持内部运作。用户会看到一段
  由专家型子 Agent组成的连贯回复。
- **依用途分流 sink**。 把给用户看的回答送到 stdout，把除错笔记送到
  写档的 `logs` named output，把最终产物送到 Discord webhook。
- **后处理文字**。 配置 `controller_direct: false`，再加上一个自订输出，
  在控制器文字抵达用户之前先清理、翻译或加上浮水印。
- **与传输层无关的代码**。 同一个 Creature可以跑在 CLI、web 或桌面环境，
  因为输出层已把传输抽象化了。

## 不要被它框住

没有输出的 Creature 也是合理的：有些 trigger 只会造成副作用（写档、寄 email）。
反过来说，输出也可以是完整模块 — 一个 Python 模块甚至可以决定执行一个
迷你 Agent，来选择每个 chunk 应该如何格式化。这听起来很夸张，而且通常也
确实如此，但它是可行的。

## 另见

- [子 Agent](sub-agent.md) — `output_to: external` 会直接经过 router 串流。
- [控制器](controller.md) — 真正喂数据给 router 的地方。
- [reference/builtins.md — Outputs 参考](../../reference/builtins.md) — 内建列表。
- [guides/custom-modules.md 指南](../../guides/custom-modules.md) — 如何撰写你自己的模块。
