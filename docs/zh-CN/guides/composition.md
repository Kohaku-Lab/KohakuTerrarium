---
title: 组合代数
summary: 用顺序 / 并行 / 回退 / 重试运算符，在纯 Python 里把 Agent 和异步可调用对象拼起来。
tags:
  - guides
  - python
  - composition
---

# 组合

写给想在纯 Python 里做多 Agent 编排、又不想搭一个 terrarium 的读者。

组合代数把 Agent 和异步可调用对象当成可组合的单元。四个运算符
（`>>`、`&`、`|`、`*`）覆盖顺序、并行、回退和重试。一切都返回
`BaseRunnable`，可以继续往下组合。

概念入门：[组合代数](../concepts/python-native/composition-algebra.md)、
[Agent 作为 Python 对象](../concepts/python-native/agent-as-python-object.md)。

当你想要的循环在生物 (creature) 外面时用这份指南：作者 ↔ 评审循环到
通过为止、并行集成、从便宜到昂贵的回退链。需要共享频道的横向多
Agent 系统时，用 [Terrarium](terrariums.md)。

## 运算符

| 运算符 | 含义 |
|---|---|
| `a >> b` | 顺序：`b(a(x))`。自动展平。右侧是 dict 则变成 `Router`。 |
| `a & b` | 并行：两个并发运行，返回结果**元组**。首个失败发生时，存活的兄弟会先被取消并 await 完毕，异常才向外传播。 |
| `a \| b` | 回退：`a` 抛错时，用原始输入运行 `b`。`b` 也失败时，`a` 的异常作为 `__cause__` 链上。 |
| `a * N` | 重试：出异常时最多尝试 `N` 次（立即重试，无延迟）。 |

优先级遵循 Python 运算符：`*` 最紧，其次 `>>`，再 `&`，最后 `|`。
所以 `a >> b & c` 是 `(a >> b) & c`，`a & b | c` 是 `(a & b) | c`。
拿不准就加括号。

组合子与方法：

- `Pure(fn)` / `pure(fn)`：包装一个普通的同步或异步可调用对象。
- `.retry(max_attempts, *, backoff=0.0, max_backoff=30.0)`：类似
  `* N`，但带指数退避：第一次失败后睡 `backoff` 秒，每次翻倍，
  上限 `max_backoff`。
- `.map(fn)`：后置变换输出（`self >> pure(fn)`）。
- `.contramap(fn)`：前置变换输入（`pure(fn) >> self`）。
- `.fails_when(pred)`：输出命中谓词时抛 `ValueError`
  （可与 `|` 组合）。
- `pipeline.iterate(initial_input)`：异步迭代器，把每次输出回喂为
  下一次输入；`it.feed(value)` 可覆盖下一次输入。

## `agent` 与 `factory`

两个 Agent 包装器，接受同样的关键字参数：

```python
await agent(config, *, engine=None, pwd=None, llm=None)   # -> AgentRunnable
factory(config, *, engine=None, pwd=None, llm=None)       # -> AgentFactory
```

- `config`：`AgentConfig`、文件系统路径，或
  `@pkg/creatures/<name>` 引用。
- `engine`：要生成到的共享 `Terrarium`。省略时，每个包装器会
  自己起一个私有引擎、并随 runnable 一起拆掉；传共享引擎可以在多个
  compose Agent 之间摊薄启动成本（此时关闭 runnable 只移除它的生物，
  绝不动你的引擎）。
- `pwd`：生物的工作目录（无全局 chdir）。
- `llm`：profile 名、`LLMProfile` 或提供商实例，与
  `Agent.build` / `Terrarium.add_creature` 的语法相同。

`agent(...)` 是**持久的**：立即启动，对话跨调用累积，必须关闭
（用 `async with`）。`factory(...)` 是**按调用的**：每次调用一个全新
Agent，无状态延续，也没有需要管理的生命周期。

```python
from kohakuterrarium.compose import agent, factory

async with await agent("@kt-biome/creatures/swe", llm="fast") as swe:
    r1 = await swe("Read the repo.")
    r2 = await swe("Now fix the auth bug.")   # same conversation

coder = factory(some_config)
r1 = await coder("Task 1")                    # fresh agent
r2 = await coder("Task 2")                    # another fresh agent
```

构造是严格的：错误路径抛 `ConfigNotFoundError`，未安装的包抛
`PackageNotInstalledError`，错误的 `llm` 选择器抛
`LLMNotConfiguredError`；这些都在 `agent()` / 第一次调用 `factory` 时就抛，
而不是稍后变成一个空回复。

## 作者 ↔ 评审循环

迭代一条双 Agent 流水线，直到评审通过：

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

`.iterate()` 把流水线的输出回喂为下一次输入，产生一个可以用原生
`async for` 循环的异步流。

## 并行集成 + 择优

三个 Agent 并行跑，留最长的答案：

```python
from kohakuterrarium.compose import factory

fast = factory(make("fast", "Answer concisely."))
deep = factory(make("deep", "Answer thoroughly."))
creative = factory(make("creative", "Answer imaginatively."))

ensemble = (fast & deep & creative) >> (lambda results: max(results, key=len))
best = await ensemble("What is recursion?")
```

三个并发运行，你付出的是最大延迟，不是延迟之和。
积的结果是元组，按分支顺序排列。某个分支抛错时，其他分支会先被
取消（并 await 完毕），异常才向外传播，不会留下脱缰的 Agent 继续
烧 LLM 轮次。

## 重试 + 回退链

昂贵的专家试两次，再回退到便宜的通才：

```python
safe = (expert * 2) | generalist
result = await safe("Explain JSON-RPC.")
```

带尝试间退避：

```python
safe = expert.retry(3, backoff=2.0, max_backoff=30.0) | generalist
```

再配上错误谓词式回退：

```python
cheap = fast.fails_when(lambda r: len(r) < 50)
pipeline = cheap | deep            # if fast returns < 50 chars, try deep
```

整条链都失败时，你接到的异常会把主分支的失败带在 `__cause__` 上，
调试时原始错误不会丢。

## 路由

`>>` 右侧的 dict 会变成 `Router`：

```python
router = classifier >> {
    "code":   coder,
    "math":   solver,
    "prose":  writer,
    "_default": generalist,       # optional catch-all
}
```

路由器根据上游输出选键：二元组 `(key, payload)` 把 `payload` 路由到
名为 `key` 的分支；其他值则同时作为键和载荷。没有匹配分支也没有
`_default` 时抛 `KeyError`。

## Agent 与函数混搭

普通可调用对象自动包成 `Pure`：

```python
pipeline = (
    writer
    >> str.strip                      # plain callable on the output
    >> (lambda t: f"Review:\n{t}")    # lambda
    >> reviewer
    >> json.loads                     # parse reviewer's JSON response
)
```

同步、异步都行；异步会自动 await。

## 什么时候改用 terrarium

选 terrarium，当：

- 生物需要*持续*运行，按自己的节奏响应消息。
- 你需要热插拔生物或外部可观测性。
- 多个生物共享一个工作区（便笺、频道），需要 `Environment` 隔离。

选组合，当：

- 你的应用是编排者，按需调用 Agent。
- 流水线是短命的（请求级，不是长驻的）。
- 你想要原生 Python 控制流（`for`、`if`、`try`、`gather`）。

两者可以混用：给 `agent()` / `factory()` 传 `engine=`，compose 流水线
的生物就会生成到你长驻 terrarium 用的那个引擎里。

## 故障排查

- **持久 `agent()` 关闭后复用就抛错。**它是异步上下文管理器，
  所有调用都放在 `async with` 里。
- **流水线莫名其妙返回元组。**你某处用了 `&`；结果是元组。加
  `>> (lambda results: ...)` 来收拢。
- **重试不重试。**`* N` 只在异常时触发。用
  `.fails_when(pred)` 把“看着不对的成功”变成异常。
- **步骤之间类型不匹配。**每一步的输出就是下一步的输入。插一个
  `pure` 函数（或 lambda）做适配。

## 另请参阅

- [编程式用法](programmatic-usage.md)：底层的 `Agent` / `Terrarium` / `Creature` API。
- [概念 / 组合代数](../concepts/python-native/composition-algebra.md)：设计动机。
- [参考 / Python API](../reference/python.md#compose)：导出与运算符签名。
- [`examples/code/`](../../../examples/code/)：`review_loop.py`、`ensemble_voting.py`、`debate_arena.py`、`smart_router.py`、`pipeline_transforms.py`。
