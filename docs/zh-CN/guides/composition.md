---
title: 组合代数
summary: 在纯 Python 中使用序列／平行／后备／重试运算子，将 agent 与非同步 callable 串接在一起。
tags:
 - guides
 - python
 - composition
---

# 组合

给想直接从纯 Python 进行多 agent 编排、而不想先建立Terrarium的读者。

组合代数将 agent 与非同步 callable 视为可组合的单元。四个运算子（`>>`、`&`、`|`、`*`）分别涵盖序列、平行、后备与重试。所有结果都会回传一个你可以继续组合的 `BaseRunnable`。

概念预习：[组合代数](../concepts/python-native/composition-algebra.md)、[作为 Python 物件的 agent](../concepts/python-native/agent-as-python-object.md)。

当你想把回圈放在 creature 外面时，请使用本指南——例如 writer ↔ reviewer 直到通过、平行 ensemble、由便宜到昂贵的后备链。若你要的是具有共享频道的横向多 agent 系统，请改用[Terrarium 指南](terrariums.md)。

## 运算子

| Op | 意义 |
|---|---|
| `a >> b` | 序列：`b(a(x))`。会自动摊平。右侧若为 dict，会转成 `Router`。 |
| `a & b` | 平行：`asyncio.gather(a(x), b(x))`。回传 list。 |
| `a \| b` | 后备：若 `a` 丢出例外，则改试 `b`。 |
| `a * N` | 若发生例外，最多额外重试 `a` `N` 次。 |

优先顺序：`*` > `|` > `&` > `>>`。

组合器：

- `Pure(fn_or_value)` — 包装一般 callable。
- `.map(fn)` — 对输出做后置转换。
- `.contramap(fn)` — 对输入做前置转换。
- `.fails_when(pred)` — 当 predicate 命中时丢出例外（可与 `|` 组合）。
- `pipeline.iterate(stream)` — 将 pipeline 套用到 async iterable 的每个元素。

## `agent` 与 `factory`

两种 agent 包装器：

- `agent(config_or_path)` — **持久型** agent（async context manager）。对话上下文会在多次调用间累积。适合单次较长的互动。
- `factory(config)` — **逐次调用** agent。每次调用都建立全新的 agent；不会承接状态。适合无状态 worker。

```python
from kohakuterrarium.compose import agent, factory

async with await agent("@kt-biome/creatures/swe") as swe:
    r1 = await swe("Read the repo.")
    r2 = await swe("Now fix the auth bug.")   # same conversation

coder = factory(some_config)
r1 = await coder("Task 1")                    # fresh agent
r2 = await coder("Task 2")                    # another fresh agent
```

## Writer ↔ reviewer 回圈

反复执行一条双 agent pipeline，直到 reviewer 核准：

```python
import asyncio
from kohakuterrarium.compose import agent
from kohakuterrarium.core.config import load_agent_config

def make(name, prompt):
    c = load_agent_config("@kt-biome/creatures/general")
    c.name, c.system_prompt = name, prompt
    c.tools, c.subagents = [], []
    return c

async def main():
    async with await agent(make("writer", "You are a writer.")) as writer, \
               await agent(make("reviewer", "Strict reviewer. Say APPROVED when good.")) as reviewer:

        pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer

        async for feedback in pipeline.iterate("Write a haiku about coding."):
            print(f"Reviewer: {feedback[:120]}")
            if "APPROVED" in feedback:
                break

asyncio.run(main())
```

`.iterate()` 会将 pipeline 的输出回灌为下一次输入，产生一个可用原生 `async for` 回圈处理的 async stream。

## 平行 ensemble 与挑选最佳结果

平行执行三个 agent，保留最长的答案：

```python
from kohakuterrarium.compose import factory

fast = factory(make("fast", "Answer concisely."))
deep = factory(make("deep", "Answer thoroughly."))
creative = factory(make("creative", "Answer imaginatively."))

ensemble = (fast & deep & creative) >> (lambda results: max(results, key=len))
best = await ensemble("What is recursion?")
```

`&` 会派发到 `asyncio.gather`，因此三者会并行执行，你付出的会是最大延迟，而不是总和。

## 重试 + 后备链

先让昂贵的 expert 试两次，再后备到便宜的 generalist：

```python
safe = (expert * 2) | generalist
result = await safe("Explain JSON-RPC.")
```

也可搭配基于错误条件的后备：

```python
cheap = fast.fails_when(lambda r: len(r) < 50)
pipeline = cheap | deep            # if fast returns < 50 chars, try deep
```

## 路由

`>>` 右手边若是 dict，会变成 `Router`：

```python
router = classifier >> {
    "code":   coder,
    "math":   solver,
    "prose":  writer,
}
```

上游步骤应输出一个 dict `{classifier_key: payload}`；router 会挑选对应的分支。很适合「先分类，再派发」这类模式。

## 混用 agent 与函式

一般 callable 会自动以 `Pure` 包装：

```python
pipeline = (
    writer
    >> str.strip                      # zero-arg callable on the output
    >> (lambda t: {"text": t})        # lambda
    >> reviewer
    >> json.loads                     # parse reviewer's JSON response
)
```

同步与非同步 callable 都能使用；若为 async，会自动 await。

## Side-effect logging

```python
from kohakuterrarium.compose.effects import Effects

effects = Effects()
logged = effects.wrap(pipeline, on_call=lambda step, x, y: print(f"{step}: {x!r} -> {y!r}"))
result = await logged("input")
```

这对于除错 pipeline 流程很有用，而且不需要改动 pipeline 本身。

## 何时应改用 terrarium

以下情况适合选 terrarium：

- Creatures 需要*持续*执行，并依自己的排程对消息作出反应。
- 你需要热插拔 creatures，或需要外部可观测性。
- 多个 creatures 共用同一个工作空间（scratchpad、频道），且需要 `Environment` 隔离。

以下情况适合选 composition：

- 你的应用程式本身就是协调者，并按需调用 agents。
- pipeline 生命周期很短（以 request 为范围，而非长时间执行）。
- 你想使用原生 Python 控制流程（`for`、`if`、`try`、`gather`）。

## 疑难排解

- **持久型 `agent()` 在重复使用时抛出异常**。 它是 async context manager——请放在 `async with` 内使用。
- **Pipeline 意外返回 list**。 你在某处用了 `&`；结果会是 list。加上 `>> (lambda results: ...)` 将其收敛。
- **Retry 没有重试**。 `* N` 只会在发生例外时触发。请用 `.fails_when(pred)` 将「看起来像失败的成功」转成例外。
- **步骤之间类型不兼容**。 每一步的输出都会成为下一步的输入。插入一个 `Pure` 函式（或 lambda）来转接。

## 另请参见

- [程序化使用指南](programmatic-usage.md) — `Terrarium`、`Creature`、`Studio` 与底层 `Agent` API。
- [概念 / 组合代数](../concepts/python-native/composition-algebra.md) — 设计理由。
- [参考 / Python API](../reference/python.md) — `compose.core`、`compose.agent`、运算子签章。
- [examples/code/](../../examples/code/) — `review_loop.py`、`ensemble_voting.py`、`debate_arena.py`、`smart_router.py`、`pipeline_transforms.py`。
