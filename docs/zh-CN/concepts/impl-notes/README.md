---
title: 实现笔记
summary: 特定子系统实际运作细节的深入解析 — 给贡献者与好奇的读者。
tags:
  - concepts
  - impl-notes
  - internals
---

# 实现笔记

这些文件不是用户必读；它们解释的是 **某些子系统实际上是怎么写的**，而不是怎么用它。适合想贡献框架或想搞清楚「为什么这个设计是这个样子」的读者。

- [提示词组合](prompt-aggregation.md) — system prompt 是怎么从人格/提示词、工具清单、框架 hint、按需加载的 skill 组出来的。
- [串流解析](stream-parser.md) — 用状态机把 LLM 输出解析成文字、工具调用、子 Agent派遣、框架指令。
- [非阻塞压缩](non-blocking-compaction.md) — 控制器继续跑的同时，summariser 在背景重建压缩后的对话，切换点在回合之间。
- [会话持久化](session-persistence.md) — `.kohakutr` 文件格式、每个 Creature存什么、恢复时怎么重建对话状态。
