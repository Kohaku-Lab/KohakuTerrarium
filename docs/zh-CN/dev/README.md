---
title: 开发指南
summary: 面向贡献者的文档 —— 介绍内部结构、依赖图、前端架构和测试策略。
tags:
  - dev
  - overview
---

# 开发指南

这部分文档面向框架贡献者，而不是普通用户。如果你只是想使用 KohakuTerrarium 运行 Agent，请返回 [使用指南](../guides/README.md)。

## 章节

- [框架内部机制](internals.md) —— 介绍运行时的整体装配方式，包括事件队列、控制器循环、executor、子代理管理和插件封装。
- [依赖图](dependency-graph.md) —— 说明模块 import 方向的不变量，以及用于强制校验这些规则的测试。
- [前端架构](frontend.md) —— 介绍 Vue 3 仪表板的布局、状态 store、WebSocket 连接方式，以及如何贡献 UI 变更。
- [测试](testing.md) —— 说明测试目录结构、`ScriptedLLM` 与 `TestAgentBuilder` 辅助工具，以及如何编写具备确定性的 Agent 测试。

## 项目治理

- 贡献流程：Code of Conduct 和 CONTRIBUTING 指南都在仓库根目录。
- 发布节奏：参见 [ROADMAP](https://github.com/Kohaku-Lab/KohakuTerrarium/blob/main/ROADMAP.md)，了解已完成和正在探索的方向。
