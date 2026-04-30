---
title: 开发指南
summary: 面向任务的 how-to 文档：编写 Creature、将它们组合起来、部署 agent。
tags:
 - guides
 - overview
---

# 开发指南

指南是面向任务的 how-to 文档。每一篇指南都回答一个具体问题 —— “我该如何设置继承？”、“我该如何把记忆加到 Creature 上？”、“这个 Creature 要如何上线？”。

与教程不同，指南不会从零开始；它默认你已经有一个可运行的 agent 实例，现在需要为它增加功能或调整行为。
它也不同于参考文档；指南的目的在于“帮助你做出更好的选择”，而不是穷举所有字段。

## 入门

- [快速开始指南](getting-started.md) — 安装框架、安装 kt-biome、启动一个 agent。

## 编写

- [配置指南](configuration.md) — 介绍 Creature 配置的结构、继承、prompt 链以及常用字段。
- [Creatures 指南](creatures.md) — 提示词设计、工具与子代理挑选、LLM 配置、发布 Creature 供他人重用。
- [Terrarium 指南](terrariums.md) — 横向多代理协作，频道、输出接线、根代理、热插拔、观察。
- [组合代数](composition.md) — 用 Python 的 `>>`、`&`、`|`、`*` 把 agent 与 async callable 串起来。
- [程序化使用指南](programmatic-usage.md) — 在你自己的 Python 代码里驱动 `Terrarium`、`Creature`、`Studio` 与底层 `Agent`。

## 存续

- [会话与恢复指南](sessions.md) — `.kohakutr` 文件如何工作、如何恢复 Creature、如何回放对话历史。
- [记忆指南](memory.md) — 会话上的 FTS5 + vector 搜索、embedding 提供者、检索模式。

## 扩展

- [插件指南](plugins.md) — prompt 插件与 lifecycle 插件的用法、组合方式与使用时机。
- [子代理指南](sub-agents.md) — 内置与内联专家、运行时预算插件和自动压缩。
- [自定义模块指南](custom-modules.md) — 自定义输入、触发器、工具、输出、子代理的写法与注册。
- [MCP 指南](mcp.md) — 连接 Model Context Protocol 服务器，把它们的工具暴露给 Creature。

## 发布与部署

- [包指南](packages.md) — 通过 `kt install` 安装、`kohaku.yaml` manifest、`@pkg/` 参照、发布你自己的包。
- [服务部署指南](serving.md) — `kt serve` 提供的 HTTP API + WebSocket + Web Dashboard、`kt app` 提供桌面版。
- [前端布局指南](frontend-layout.md) — Vue 3 Dashboard 的组织方式、在哪里扩展、事件从后端流到 UI 的路径。
- [示例指南](examples.md) — 内置示例 Creature、Terrarium、代码的阅读指引 — 建议先看哪些、为什么。
