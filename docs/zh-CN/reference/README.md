---
title: 参考
summary: 完整规格 — 每一个字段、指令、端点、hook、Python 入口点。
tags:
  - reference
  - overview
---

# 参考

参考文档面向“我知道自己要找什么，只需要准确格式”的读者。这里不解释原因，也不演示用法；这些内容请分别参见 [使用指南](../guides/README.md) 和 [核心概念](../concepts/README.md)。

## 章节

- [CLI 参考](cli.md) — `kt` 的所有子命令 (run、resume、login、install、list、info、model、embedding、search、terrarium、serve、app…)。
- [配置参考](configuration.md) — Creature、Terrarium、LLM 配置、MCP 服务器、上下文压缩、插件、输出接线的所有字段。
- [内置模块参考](builtins.md) — 内置的工具、子代理、trigger、输入、输出的参数、行为与默认值。
- [内建插件参考](builtin-plugins.md) — sandbox、budget、permgate、compact.auto 的 hook、options、行为。
- [Python API 参考](python.md) — `kohakuterrarium` 套件的公开接口：`Terrarium`、`Creature`、`Studio`、底层 `Agent`、`compose`、测试辅助工具。
- [插件 Hook 参考](plugin-hooks.md) — 插件可注册的全部生命周期 Hook、触发时机和 payload 内容。
- [HTTP API 参考](http.md) — `kt serve` 的 REST 端点与 WebSocket 通道，以及 request / response 结构。
- [v1.3.0 发布说明](release-notes-1.3.0.md) — 最终 changelog、兼容性说明、发布流程与验证摘要。
