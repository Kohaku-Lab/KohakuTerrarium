---
title: Agent 作为 Python 对象
summary: 为什么每个 Agent 都是 Python 对象、这样会解锁什么能力，以及嵌入式使用和执行 CLI 有何不同。
tags:
  - concepts
  - python
  - embedding
---

# Agent 作为 Python 对象

## 它是什么

在 KohakuTerrarium 里，Agent 不是一份配置文件——配置文件只是描述它而已。一只正在运作的 Agent 是一个 `kohakuterrarium.core.Agent.Agent` 实例：一个 async Python 对象，你可以建构它、启动它、喂它事件，再把它停掉。子 Agent是同样的对象，只是嵌套存在。terrarium 则是另一个 Python 对象，持有好几只这样的 Agent。

所有东西都可以被调用、被 await、被组合。

## 为什么这很重要

大多数 Agent 系统都暴露两层：

1. 一层配置层（YAML、JSON）来描述「这只 Agent」。
2. 一个 runtime（通常是 server 或 CLI）去读配置并产生行为。

而你真正想建立在上面的行为，通常又得放进第三层——另一个 process、另一个 container、另一套 plugin system。为了做一件其实可以只是函数调用的事情，却多了很多跳跃。

KohakuTerrarium 把这些层折叠起来：你可以直接 `import kohakuterrarium`、加载配置、spawn 一个 Agent、调用它，并且任意处理它吐出的事件。Agent 是一个 value；value 可以放进其他 value 里。

## 关键介面长什么样子

```python
from kohakuterrarium.core.Agent import Agent

Agent = Agent.from_path("@kt-biome/creatures/swe")
Agent.set_output_handler(lambda text: print(text, end=""), replace_default=True)

await Agent.start()
await Agent.inject_input("Explain what this codebase does.")
await Agent.stop()
```

或者用比较适合 transport 的包装：

```python
from kohakuterrarium.serving.Agent_session import AgentSession

session = AgentSession(Agent.from_path("@kt-biome/creatures/swe"))
await session.start()
async for event in session.send_input("What does this do?"):
  print(event)
await session.stop()
```

Terrarium 也是同样的形状：

```python
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.terrarium.config import load_terrarium_config

runtime = TerrariumRuntime(load_terrarium_config("@kt-biome/terrariums/swe_team"))
await runtime.start()
await runtime.run()
await runtime.stop()
```

## 因此你可以做什么

真正的回报不是「Agent 是 Python」——而是「因为 Agent 是 Python，而模块也是 Python，所以你可以把 Agent 放进任何模块里」。几个具体 pattern：

### Plugin 里放 Agent（智慧护栏）

做一个 `pre_tool_execute` plugin，实现内容是跑一只小型嵌套 Agent 来判断是否允许工具调用。外层Creature的主对话可以保持清晰；护栏自己的推理在自己的上下文里完成。

### Plugin 里放 Agent（无缝记忆）

一个 `pre_llm_call` plugin 先跑一只很小的 retrieval Agent，去搜寻 session 的事件日志（或外部向量数据库），挑出相关的过去内容，然后把它注入 LLM 消息。从外层Creature的角度看，它的记忆只是「自然地变好了」。

### Trigger 里放 Agent（自适应观察者）

不是写 `timer: 60s`，而是做一个自订 trigger，在 `fire()` 本体里每次 tick 都跑一只小 Agent。这只 Agent 会看目前状态，决定是否该唤醒外层Creature。这种环境感知式智慧，不需要依赖固定规则。

### Tool 里放 Agent（上下文隔离的专家）

做一个工具，调用时会 spawn 一只全新的 Agent 来完成工作。对 LLM 来说，它调用这个工具的方式跟其他工具完全一样；但从实现面看，这个工具本身就是一整套子系统。当你需要完全隔离的子系统——不同模型、不同工具、不同 prompt——这就很好用。

### Output 模块里放 Agent（路由接待员）

做一个 output 模块，专门决定每一段文字该送去哪里。简单规则可以用 switch statement；如果路由判断很细腻，就接一只 Agent 进来读串流并做决策。

## 这让哪些交叉引用成为可能

[patterns](../patterns.md) 文件会用最小片段把这些做法逐一写开。这份概念文件存在的目的，是要讲清楚：*这些都不是特殊技巧*。它们只是「Agent 是第一等 Python value」的直接应用。

## 不要被边界绑住

你不一定要用 Python 来建立Creature——在大多数情况下，只靠配置文件就够了。但如果某份Creature配置撞上墙，让你开始想要「在 Agent 正在执行的一个步骤里，再放进一只会做判断的 Agent」，那个 Python 基底其实早就在那里了，不需要再发明一套新的 plugin system。

## 延伸阅读

- [Composition algebra](composition-algebra.md) — 给 Python 端 pipeline 用的操作子。
- [Patterns](../patterns.md) — 这些能力解锁出的意外用法。
- [guides/programmatic-usage.md 指南](../../guides/programmatic-usage.md) — 这一页的任务导向版本。
- [reference/python.md 参考](../../reference/python.md) — 签章与 API 索引。
