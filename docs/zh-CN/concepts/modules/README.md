---
title: 模块
summary: 一篇文档介绍一个模块 — 控制器、输入、触发器、工具、子 Agent、输出，外加横跨多处的模块。
tags:
  - concepts
  - modules
  - overview
---

# 模块

一个 Creature由六个「一等公民」模块组成，加上几个横跨多处、没有清晰塞进六模块分类的模块：频道、会话 / 环境、记忆与压缩、插件。

## 六模块

- [控制器](controller.md) — 推理回圈：接 LLM 串流、解析工具调用、派发回馈。
- [输入](input.md) — 特殊的触发器，把用户消息带进事件队列。
- [触发器](trigger.md) — 任何不是用户输入的事件来源：计时器、idle、频道、webhook、监控条件。
- [工具](tool.md) — 有名字的能力，LLM 可以带参数调用：shell 指令、文件编辑、网页搜寻…
- [子 Agent](sub-agent.md) — 由父Creature派生出来、上下文独立、只持有父代理工具子集的嵌套Creature。
- [输出](output.md) — 路由器，接控制器产生的所有东西 (文字、工具活动、token 用量) 并分流到多个 sink。

## 横跨多处的模块

- [频道](channel.md) — 具名的广播管道，支撑多 Agent 与跨模块通讯。
- [会话与环境](session-and-environment.md) — 每个 Creature的私有状态 (session) vs. 整个Terrarium共享的状态 (environment)。
- [记忆与压缩](memory-and-compaction.md) — 会话 store 同时做为可搜寻的记忆；非阻塞的压缩怎么让上下文保持在预算内。
- [插件](plugin.md) — 修改模块之间 **连接** 的代码 — prompt 插件与 lifecycle 插件。
