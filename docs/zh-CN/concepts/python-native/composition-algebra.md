---
title: 组合代数
summary: 四个操作子与一组 combinators，把 Agents 与 async callables 当成可组合单元。
tags:
  - concepts
  - python
  - composition
---

# 组合代数

## 它是什么

一旦 Agent 变成 Python value，你就会想把它们接起来。 **组合代数（compose algebra）** 是一小组操作子与 combinators，用来把 Agents（以及任何 async callable）视为可组合的单元：

- `a >> b` — 串接（`a` 的输出变成 `b` 的输入）
- `a & b` — 平行（两者一起跑，返回 `[result_a, result_b]`）
- `a | b` — 后备（如果 `a` 丢异常，就改试 `b`）
- `a * N` — 重试（失败时额外最多重试 `N` 次）
- `pipeline.iterate(stream)` — 对 async iterable 的每个元素套用整条 pipeline；如果想形成回圈，也可以把输出回灌成输入

所有结果都会返回一个 `BaseRunnable`，所以你可以继续往下组。

## 为什么它存在

Creature内部的控制器本来就是一个回圈。但有时候你想要的是一个 *在Creature外面* 的回圈——例如 writer ? reviewer 一直来回直到核准、平行 ensemble 挑出最佳答案、跨 provider 做 retry-with-fallback。这些事情用裸的 `asyncio.gather` 和 `try/except` 当然做得到，但会把调用端代码弄得很杂。

这些操作子本质上只是包在 asyncio 外面的语法糖。它们没有引入新的执行模型；只是让「组合两只 Agent」读起来更像「把两个数字相加」。

## 我们怎么定义它

`BaseRunnable.run(input) -> Any`（async）是这套协定。任何实现了它的东西都可以被组合。

这些操作子分别是：

- `__rshift__`：把两侧包进 `Sequence`（会自动摊平嵌套 sequence；如果右侧是 dict，则会变成 `Router`）
- `__and__`：包进 `Product`；`run(x)` 会对所有分支做 `asyncio.gather`，并把 `x` 广播成共同输入
- `__or__`：包进 `Fallback`；发生异常时就往下掉
- `__mul__`：包进 `Retry`；发生异常时最多重跑 N 次

再加上一些 combinators：

- `Pure(value)` — 包住一个普通 value 或 callable；忽略输入。
- `Router(routes)` — 输入 `{key: value}` 时，派发到对应的 runnable。
- `.map(fn)` — 先转换输入（`contramap`）。
- `.contramap(fn)` — 再转换输出。
- `.fails_when(pred)` — 当 predicate 命中时丢出异常；搭配 `|` 很有用。

Agent factories：

- `Agent(config)` — 把持久型 Agent 包成 runnable。对话上下文会跨调用累积。
- `factory(config)` — 每次调用都新建 Agent。每次 invocation 都 spawn 一只新的 Agent；不保留持久状态。

## 我们怎么实现它

`compose/core.py` 放的是基础协定与 combinator classes。`compose/Agent.py` 把 Agent 包成 runnable。`compose/effects.py` 则是可选的 instrumentation，用来记录 pipeline 上的 side-effects。

Agent-factory wrappers 会处理生命周期模板代码——进入／离开时 start / stop 底层的 `Agent`，并透过 `inject_input` 加上输入、收集输出。

## 一个真实范例

```python
import asyncio
from kohakuterrarium.compose import Agent, factory
from kohakuterrarium.core.config import load_Agent_config

def make_Agent(name, prompt):
  c = load_Agent_config("@kt-biome/creatures/general")
  c.name, c.system_prompt, c.tools, c.sub-agents = name, prompt, [], []
  return c

async def main():
  async with await Agent(make_Agent("writer", "You are a writer.")) as writer, \
  await Agent(make_Agent("reviewer", "You are a strict reviewer. Say APPROVED if good.")) as reviewer:

  pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer

  async for feedback in pipeline.iterate("Write a haiku about coding"):
  print(f"Reviewer: {feedback[:100]}")
  if "APPROVED" in feedback:
  break

  fast = factory(make_Agent("fast", "Answer concisely."))
  deep = factory(make_Agent("deep", "Answer thoroughly."))
  safe = (fast & deep) >> (lambda results: max(results, key=len))
  safe_with_retry = (safe * 2) | fast
  print(await safe_with_retry("What is recursion?"))

asyncio.run(main())
```

两只 Agent、持久对话、回馈回圈、带有 fallback 与 retry 的平行 ensemble——全部都在一般 Python 里完成。

## 因此你可以做什么

- **Review loops**。 Writer `>>` reviewer `.iterate(...)` 直到某个 predicate 成立，不需要再写新的 orchestration code。
- **Ensembles**。 `(fast & deep) >> pick_best` —— 平行跑两只 Agent，再把结果合并。
- **Fallback chains**。 先试便宜的 provider；失败再退到更强的。
- **暂时性错误的重试**。 任何 runnable 都可以用 `* N` 包起来。
- **串流 pipeline**。 `.iterate(async_generator)` 会把每个元素都走完整条 pipeline。

## 不要被边界绑住

组合代数是可选的。对大多数嵌入式使用情境来说，Creature 配置加上 `Creature.chat()` / `Studio.sessions.chat` 就已经够了。这些操作子存在的理由，是当你 *真的* 想直接从 Python 做轻量 pipeline、又不想管理长生命周期 terrarium graph 的时候。

状态说明：这套代数很有用，但仍在演化中——操作子的精确集合未来可能会根据回馈而增加或简化。可以放心用在内部 pipeline，但如果是 production 用途，建议视为 early-stable。

## 延伸阅读

- [Agent as a Python object](agent-as-python-object.md) — 这份内容建立其上的基础。
- [Patterns](../patterns.md) — 混合组合代数与嵌入式 Agent 的用法。
- [guides/composition.md 指南](../../guides/composition.md) — 任务导向的使用方式。
- [reference/python.md — kohakuterrarium.compose 参考](../../reference/python.md) — 完整 API。
