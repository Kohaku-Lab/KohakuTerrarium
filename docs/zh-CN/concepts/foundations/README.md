---
title: 基础 (Foundations)
summary: 为什么这个框架存在、它模型里的 Agent 是什么、六个模块如何在运行时组合。
tags:
  - concepts
  - foundations
---

# 基础

这一组文件回答三个问题：

1. **为什么这个框架存在**？ 看 [Why KohakuTerrarium](why-kohakuterrarium.md) — 每一个 Agent 产品都会重做同一套底层机制；这个框架把那套机制独立出来一次做好。
2.**在这个框架的模型里，一只 Agent 是什么**？ 看 [什么是 Agent](what-is-an-agent.md) — 从聊天机器人出发分四个阶段，推导出六模块的 Creature结构。
3.**这六个模块实际上怎么组合**？ 看 [组合一个 Agent](composing-an-agent.md) — 六个模块如何透过一个统一的 `TriggerEvent` envelope 在运行时互动。

读完这三份文件，后面每一份核心概念文件都会有熟悉的语境。
