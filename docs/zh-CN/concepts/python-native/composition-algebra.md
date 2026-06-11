---
title: 组合代数
summary: 四个运算符加一组组合子，把 Agent 和异步可调用对象当成可组合的单元。
tags:
  - concepts
  - python
  - composition
---

# 组合代数

## 它是什么

Agent 一旦成为 Python 值，你就会想把它们接起来。**组合代数
（compose algebra）**是一小组运算符与组合子，把 Agent（以及任何
异步可调用对象）当成可组合的单元：

- `a >> b`：顺序（`a` 的输出变成 `b` 的输入）
- `a & b`：并行（一起跑，返回 `[result_a, result_b]`）
- `a | b`：回退（`a` 抛错就试 `b`）
- `a * N`：重试（失败时最多再试 `N` 次）
- `pipeline.iterate(stream)`：对异步可迭代对象的每个元素套用整条
  流水线；想形成循环时也可以把输出回喂为输入

一切都返回 `BaseRunnable`，可以继续往下组。

## 为什么存在

生物 (creature) 内部的控制器本来就是个循环。但有时你想要的循环在
*生物外面*：作者 ↔ 评审来回迭代直到通过、并行集成挑出最佳答案、
跨提供商的重试加回退。用裸的 `asyncio.gather` 和 `try/except` 当然
做得到，但会把调用处弄得很乱。

这些运算符只是 asyncio 之上的人体工学语法糖。它们没有引入新的执行
模型，只是让“组合两个 Agent”读起来像“把两个数相加”。

## 我们怎么定义它

协议是 `BaseRunnable.run(input) -> Any`（异步）。实现了它的东西都
可以组合。

运算符：

- `__rshift__` 把两侧包进 `Sequence`（自动展平嵌套的 sequence；
  右侧是 dict 则变成 `Router`）。
- `__and__` 包进 `Product`；`run(x)` 对所有分支 `asyncio.gather`，
  把 `x` 广播为共同输入。
- `__or__` 包进 `Fallback`；出异常时落到下一个。
- `__mul__` 包进 `Retry`；出异常时最多重跑 N 次。

外加组合子：

- `Pure(value)`：包住一个普通值或可调用对象；忽略输入。
- `Router(routes)`：输入 `{key: value}` 时派发到匹配的 runnable。
- `.map(fn)`：前置变换输入（`contramap`）。
- `.contramap(fn)`：后置变换输出。
- `.fails_when(pred)`：谓词命中时抛错；与 `|` 搭配很有用。

Agent 工厂：

- `agent(config)`：把持久 Agent 包成 runnable。对话上下文跨调用累积。
- `factory(config)`：按调用的 Agent。每次调用生成一个全新 Agent；
  没有持久状态。

## 我们怎么实现它

`compose/core.py` 放基础协议与组合子类。`compose/agent.py` 把 Agent
包成 runnable。

Agent 工厂包装器处理生命周期样板：进入/离开时 start / stop 底层
`Agent`，并通过 `inject_input` 转发输入、收集输出。

## 一个真实示例

```python
import asyncio
from kohakuterrarium.compose import agent, factory
from kohakuterrarium.core.config import load_agent_config

def make_agent(name, prompt):
    c = load_agent_config("@kt-biome/creatures/general")
    c.name, c.system_prompt, c.tools, c.subagents = name, prompt, [], []
    return c

async def main():
    async with await agent(make_agent("writer", "You are a writer.")) as writer, \
               await agent(make_agent("reviewer", "You are a strict reviewer. Say APPROVED if good.")) as reviewer:

        pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer

        async for feedback in pipeline.iterate("Write a haiku about coding"):
            print(f"Reviewer: {feedback[:100]}")
            if "APPROVED" in feedback:
                break

    fast = factory(make_agent("fast", "Answer concisely."))
    deep = factory(make_agent("deep", "Answer thoroughly."))
    safe = (fast & deep) >> (lambda results: max(results, key=len))
    safe_with_retry = (safe * 2) | fast
    print(await safe_with_retry("What is recursion?"))

asyncio.run(main())
```

两个 Agent、持久对话、反馈循环、带回退与重试的并行集成，全部在
普通 Python 里完成。

## 因此你能做什么

- **评审循环。**作者 `>>` 评审 `.iterate(...)` 直到谓词满足。
  不用写新的编排代码。
- **集成。**`(fast & deep) >> pick_best`：并行跑两个 Agent，再合并
  结果。
- **回退链。**先试便宜的提供商；失败再退到更强的。
- **瞬时故障的重试。**任何 runnable 都能用 `* N` 包起来。
- **流式流水线。**`.iterate(async_generator)` 让每个元素走完整条
  流水线。

## 不要被边界绑住

组合代数是可选的。生物配置加上 `Studio`、`Terrarium` 或直接的
`Creature.chat()` 已经覆盖了大多数嵌入场景。这些运算符是为这种情况
准备的：你*确实*想直接在纯 Python 里做多 Agent 编排，又不想管理一张
运行时图。

状态说明：这套代数很有用，但仍在演化，运算符的精确集合可能根据
反馈增减。内部流水线放心用；生产用途请按 early-stable 对待。

## 另请参阅

- [Agent 作为 Python 对象](agent-as-python-object.md)：本文建立其上的基础。
- [模式](../patterns.md)：混合组合代数与嵌入式 Agent 的用法。
- [guides/composition.md](../../guides/composition.md)：任务导向的用法。
- [reference/python.md 的 kohakuterrarium.compose 一节](../../reference/python.md)：完整 API。
