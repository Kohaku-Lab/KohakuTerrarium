---
title: 核心概念
summary: Creature、Terrarium、频道、触发器、插件与 compose 代数的心智模型。
tags:
  - concepts
  - overview
---

# 核心概念

概念文件教的是心智模型。它不是参考——字段名称、函数签名和指令都放在 [参考](../reference/README.md)里。
它也不是使用指南——分步骤的操作说明放在 [使用指南](../guides/README.md)里。

概念文件的目的是让你理解 **这个框架为何会设计成现在这样**。读完之后，你应该能够看着一份从来没看过的配置文件，大致猜到它想做什么，而不必先回头查所有字段。

## 读的顺序

这组文档有明确的阅读顺序：

1. [基础](foundations/README.md) — 为什么这个框架存在，一个 Creature 到底是什么，以及六个模块如何在运行时组合。
2. [模块](modules/README.md) — 每个 Creature 模块一篇文档：控制器、输入、触发器、工具、子 Agent、输出，以及横跨多处的 Channel / Session / Memory / Plugin。
3. [多 Agent 系统](multi-agent/README.md) — 纵向 (子 Agent) 与横向 (Terrarium + 频道 + 输出接线) 两个轴向，何时挑哪一个。
4. [Python 原生集成](python-native/README.md) — Agent 作为一等公民的 async Python 值，以及把它们串成 pipeline 的代数。
5. [模式](patterns.md) — 组合现有模块所得到的典型用法。
6. [边界](boundaries.md) — Creature 抽象是预设值而不是铁律；框架何时可以弯曲自己的抽象；框架何时根本不适合你。
7. [词汇表](glossary.md) — 文件中用到的术语的白话解释。

## 实现笔记

不是必读，但对想理解系统实际怎么运作的人 (通常是贡献者) 很有帮助：

- [实现笔记](impl-notes/README.md) — 特定子系统的深入解析。
