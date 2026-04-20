---
title: 第一个 Python 嵌入
summary: 通过 AgentSession 与组合代数，在你自己的 Python 代码中运行 Agent。
tags:
  - tutorials
  - python
  - embedding
---

# 第一个 Python 嵌入

**问题** ： 你想在自己的 Python 应用中运行一个 Creature，获取它的输出、用代码驱动它的输入，并把它与其他代码组合起来。

**完成状态** ： 你会先完成一个最小脚本：启动 Creature、注入输入、通过自定义 handler 获取输出，并正确关闭它。接着再用 `AgentSession` 实现一次事件流版本。最后，再用同样的方式嵌入一个 Terrarium。

**先决条件** ： [第一个 Creature](first-creature.md)。你需要以可以 `import kohakuterrarium` 的方式安装这个包。

在这个框架里，Agent 不是配置文件，而是一个 Python 对象。配置文件描述 Agent；`Agent.from_path(...)` 会构造出一个 Agent；而这个对象由你持有。Sub-agents、Terrariums 与 Sessions 也是同样的形态。完整的心智模型请参考 [Agent 作为 Python 对象](../concepts/python-native/agent-as-python-object.md)。

## 第 1 步：以 editable 方式安装

目标：让你的虚拟环境可以 `import kohakuterrarium`。

在仓库根目录运行：

```bash
uv pip install -e .[dev]
```

`[dev]` extras 也会带上后续可能用到的测试辅助工具。

## 第 2 步：最小嵌入示例

目标：创建一个 Agent、启动它、喂给它一条输入，然后停止它。

`demo.py`：

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    agent = Agent.from_path("@kt-biome/creatures/general")

    await agent.start()
    try:
        await agent.inject_input(
            "In one sentence, what is a creature in KohakuTerrarium?"
        )
    finally:
        await agent.stop()


asyncio.run(main())
```

运行它：

```bash
python demo.py
```

默认的 stdout output 模块会打印响应。这里有三点值得注意：

1. `Agent.from_path` 解析 `@kt-biome/...` 的方式与 CLI 完全一致。
2. `start()` 会初始化 controller、tools、triggers 和 plugins。
3. `inject_input(...)` 就是用户在 CLI input 模块中输入消息的程序化对应形式。

## 第 3 步：接管输出

目标：不要把输出发到 stdout，而是交给你自己的代码处理。

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    parts: list[str] = []

    agent = Agent.from_path("@kt-biome/creatures/general")
    agent.set_output_handler(
        lambda text: parts.append(text),
        replace_default=True,
    )

    await agent.start()
    try:
        await agent.inject_input(
            "Explain the difference between a creature and a terrarium."
        )
    finally:
        await agent.stop()

    print("".join(parts))


asyncio.run(main())
```

`replace_default=True` 会禁用 stdout，让你的 handler 成为唯一的输出 sink。这种形态很适合 web backend、bot，或任何希望自行控制渲染方式的场景。

## 第 4 步：使用 `AgentSession` 做流式处理

目标：获得一个 chunks 的 async iterator，而不是 push handler。当你希望通过 `async for` 处理响应时，这种形式会更方便。

```python
import asyncio

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.serving.agent_session import AgentSession


async def main() -> None:
    agent = Agent.from_path("@kt-biome/creatures/general")
    session = AgentSession(agent)

    await session.start()
    try:
        async for chunk in session.chat(
            "Describe three practical uses of a terrarium."
        ):
            print(chunk, end="", flush=True)
        print()
    finally:
        await session.stop()


asyncio.run(main())
```

`AgentSession` 是 HTTP 与 WebSocket 层使用的、更适合 transport 的包装器。底层仍然是同一个 Agent；它只是让你在每次 `chat(...)` 调用时拿到一个 `AsyncIterator[str]`。

## 第 5 步：嵌入整个 Terrarium

目标：从 Python 驱动一套多 Agent 配置，而不是通过 CLI。

```python
import asyncio

from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime


async def main() -> None:
    config = load_terrarium_config("@kt-biome/terrariums/swe_team")
    runtime = TerrariumRuntime(config)

    await runtime.start()
    try:
        # runtime.run() drives the main loop until a stop signal.
        # For a script, you can interact through runtime's API or
        # just let the creatures run to quiescence.
        await runtime.run()
    finally:
        await runtime.stop()


asyncio.run(main())
```

如果你想以编程方式 **控制** 正在运行的 Terrarium（向 channel 发送消息、启动 Creature、观察消息），请使用 `TerrariumAPI`（`kohakuterrarium.terrarium.api`）。这也是 Terrarium 管理工具底层使用的同一个 facade。

## 第 6 步：把 Agent 当作值来组合

“Agent 是 Python 对象”真正有威力的地方在于，你可以把一个 Agent 放进任何其他东西中：插件、trigger、工具，甚至另一个 Agent 的 output 模块。[组合代数](../concepts/python-native/composition-algebra.md) 提供了一组运算符（`>>`、`|`、`&`、`*`）来表达常见形态，例如 sequence、fallback、parallel、retry。当一串普通函数组成的 pipeline 看起来已经很自然时，就可以考虑改用这些运算符。

## 你学到了什么

- `Agent` 就是普通的 Python 对象：创建、启动、注入输入、停止。
- `set_output_handler` 可以替换输出 sink；`AgentSession.chat()` 则把它变成 async iterator。
- `TerrariumRuntime` 也可以用同样的形式运行整套多 Agent 配置。
- CLI 只是这些对象的一个调用方；你的应用程序也可以是另一个。

## 接下来读什么

- [Agent 作为 Python 对象](../concepts/python-native/agent-as-python-object.md) —— 这个概念本身，以及它解锁的模式。
- [程序化使用指南](../guides/programmatic-usage.md) —— 面向任务的 Python 接口参考。
- [组合代数](../concepts/python-native/composition-algebra.md) —— 用于把 Agent 接入 Python pipeline 的运算符。
- [Python API 参考](../reference/python.md) —— 精确接口签名。
