---
title: 为什么是 KohakuTerrarium
summary: 每个 Agent 产品都在重写同一层基底设施，于是有了这个偏向 framework 的响应。
tags:
  - concepts
  - foundations
  - philosophy
---

# 为什么 KohakuTerrarium 存在

## 一个你大概已经观察到的现象

过去两年里，出现了非常多 Agent 产品：Claude Code、Codex、OpenClaw、Gemini CLI、Hermes Agent、OpenCode，还有很多很多。它们彼此都真的不一样：不同的工具介面、不同的控制器回圈、不同的记忆策略、不同的多 Agent设计。

但它们也都从零开始重做同一层基底：

- 一个会从 LLM 串流并解析工具调用的控制器
- 一层工具注册表与派发层
- 处理 `/loop`、背景工作、idle check 的触发器系统
- 一个为了上下文隔离而设计的子 Agent机制
- 一个或多个互动介面的输入与输出 plumbing
- session、持久化、resume
- 某种形式的多 Agent wiring

每个团队只要想尝试一种新的 Agent 形状，最后都得把这些东西再盖一次。这代表大量代码都花在重写，只为了走到真正有趣的部分：*新的设计本身*。

## 常见的逃法，以及它为什么会失败

最常见的响应是：「做一个够泛化的 Agent，让它处理所有情况。」但这条路会撞上悬崖：你涵盖的形状越多，就得加越多特例；特例越多，这个通用 Agent 就越脆弱。一年后有人又有了新想法，结果发现这个通用 Agent 装不进去，于是大家重新开始。

把「通用」建立在单一产品上，是一次失败的优化。

## 真正的动作

让 **打造一只目的明确的 Agent 变得便宜**。

如果每一种新的 Agent 形状，只需要一份配置文件、几个自订模块，以及一个清楚的心智模型，这个领域就不会一直重造轮子。那层基底——每个 Agent 都需要，而且彼此几乎一样的部分——就可以集中留在同一个地方。真正新的部分，才是你自己去写的。

那层基底就是 KohakuTerrarium：一个 **给 Agents 用的 framework**，而不是另一个 Agent。

## 这里的「基底」是什么意思

给一份具体清单方便校准：

- 一套统一的事件模型。用户输入、计时器触发、工具完成、频道消息——全都用同一种信封。
- 六模块Creature 抽象。参见 [what-is-an-Agent](what-is-an-agent.md)。
- 一层 session 系统，同时负责运行时持久化与可搜寻的知识库。
- 一个多 Agent wiring 层（terrarium），它纯粹是结构性的，本身没有自己的 LLM。
- Python-native 的组合方式：每个模块都是 Python class，每个 Agent 都是一个 async Python value。
- 开箱即用的运行时介面（CLI、TUI、HTTP、WebSocket、desktop、daemon），让你不用自己写 transport code。

这些都是你在想试一种新 Agent 设计时，不会想重写的部分。

## KohakuTerrarium 不是

- **不是 Agent 产品**。 你不会「执行 KohakuTerrarium」；你会执行一只用它建出来的 Creature。如果你想先试用现成Creature，展示用的是 [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome) 套件。
- **不是 workflow engine**。 这里没有任何地方假设你的 Agent 会照固定步骤序列前进。
- **不是通用 LLM wrapper**。 它不打算变成那个样子。

## 用一句话定位

> KohakuTerrarium 是一台拿来打造 Agents 的机器，让人们在每次想做新 Agent 时，不必都重新发明这台机器。

## 延伸阅读

- [什么是 Agent](what-is-an-agent.md) — 这个框架围绕的定义。
- [边界](../boundaries.md) — 什么情况 KT 适合，什么情况不适合。
- [kt-biome](https://github.com/Kohaku-Lab/kt-biome) — 展示用Creature与 plugin 套件。
