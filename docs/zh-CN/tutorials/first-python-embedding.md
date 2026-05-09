---
title: 第一个 Python 嵌入示例
summary: 用 Creature.chat、Studio 与组合代数，在你自己的 Python 代码中运行代理。
tags:
  - tutorials
  - python
  - embedding
---

# 第一个 Python 嵌入示例

## 你将构建什么

**完成状态：**一个最小脚本：通过 `Terrarium` 启动 Creature，用 `Creature.chat()` 串流输出，嵌入一个多 Creature Terrarium，并用 Studio 管理 session。最后附上低层 `Agent` 示例，用于自定义 output handler。

## 第 1 步：安装开发环境

从仓库根目录：

```bash
uv pip install -e .[dev]
```

`[dev]` extras 会带入稍后可能会用到的测试辅助工具。

## 第 2 步：用 `Creature.chat()` 做最小嵌入

目标：构建一只运行中的 Creature、发送一条输入、串流它的响应，并干净地关闭引擎。

`demo.py`：

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    engine, creature = await Terrarium.with_creature(
        "@kt-biome/creatures/general"
    )

    try:
        async for chunk in creature.chat(
            "In one sentence, what is a creature in KohakuTerrarium?"
        ):
            print(chunk, end="", flush=True)
        print()
    finally:
        await engine.shutdown()


asyncio.run(main())
```

运行：

```bash
python demo.py
```

注意三件事：

1. `Terrarium.with_creature` 解析 `@kt-biome/...` 的方式与 CLI 相同。
2. 单只 Creature 也由多 Creature graph 使用的同一个引擎托管。
3. `Creature.chat(...)` 是一个文字 chunk 的 async iterator。

## 第 3 步：只推输入，不消费输出

目标：从你自己的 scheduler、bot 或 event loop 喂输入。若另一个 output sink 负责渲染，就使用 `inject_input(...)`。

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    engine, creature = await Terrarium.with_creature(
        "@kt-biome/creatures/general"
    )
    try:
        await creature.inject_input(
            "Explain the difference between a creature and a terrarium."
        )
    finally:
        await engine.shutdown()


asyncio.run(main())
```

Creature 会使用它配置的 output module。若只是想在自己的代码里拿简单文字流，优先用 `Creature.chat(...)`。

## 第 4 步：用低层 `Agent` 捕获输出

目标：把输出导进你自己的 handler，而不是 stdout。这是自定义 transport 的进阶形状；大多数应用应从 `Creature.chat()` 或 `Studio.sessions.chat` 开始。

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
            "Describe three practical uses of a terrarium."
        )
    finally:
        await agent.stop()

    print("".join(parts))


asyncio.run(main())
```

`replace_default=True` 会关闭 stdout，让你的 handler 成为唯一 sink。

## 第 5 步：嵌入整个 Terrarium

目标：从 Python 驱动多代理 setup，而不是通过 CLI。

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    async with await Terrarium.from_recipe(
        "@kt-biome/terrariums/swe_team"
    ) as engine:
        swe = engine["swe"]
        async for chunk in swe.chat("Fix the auth bug."):
            print(chunk, end="", flush=True)
        print()


asyncio.run(main())
```

Recipe 会加载 Creature、宣告 channel，并接好 graph。Terrarium 自己不执行 LLM、也没有推理回圈；它负责的是结构 —— 拓扑、channel、lifecycle，以及在图变化时跟着走的 session 记账。

## 第 6 步：使用 Studio 管理 session

目标：获得与 Web dashboard / API 相同的管理层：active sessions、saved sessions、catalog、identity、attach 与 editor flows。

```python
import asyncio

from kohakuterrarium import Studio


async def main() -> None:
    async with Studio() as studio:
        session = await studio.sessions.start_creature(
            "@kt-biome/creatures/general"
        )
        cid = session.creatures[0]["creature_id"]
        stream = await studio.sessions.chat.chat(
            session.session_id,
            cid,
            "Summarize the Studio layer in one sentence.",
        )
        async for chunk in stream:
            print(chunk, end="", flush=True)
        print()


asyncio.run(main())
```

当你是在写应用 server、dashboard、editor 或多 session backend 时，`Studio` 通常是最方便的入口。

## 第 7 步：什么时候用组合代数

如果你只想把几个 callable/agent 串成短 pipeline，不想手动管理长生命周期 graph，[组合代数](../concepts/python-native/composition-algebra.md) 提供 `>>`、`|`、`&`、`*` 操作子：sequence、fallback、parallel、retry。

## 你学到了什么

- `Creature` 是由 `Terrarium` 托管的普通 Python 对象；`chat()` 会把它变成 async iterator。
- 低层 `Agent` 在你需要 `set_output_handler` 或直接事件控制时可用。
- `Terrarium` 以 graph topology 运行一只或多只 Creature。
- `Studio` 在引擎之上管理 active sessions、saved sessions、catalog、identity、attach policy 与 editor workflows。
- CLI 只是这些对象的一个使用者；你的应用也可以是另一个使用者。

## 延伸阅读

- [Agent 作为 Python 对象](../concepts/python-native/agent-as-python-object.md) — 这个概念，以及它解锁的模式。
- [程序化使用指南](../guides/programmatic-usage.md) — Python surface 的任务导向参考。
- [组合代数](../concepts/python-native/composition-algebra.md) — 把 agent 接成 Python pipeline 的操作子。
- [Python API 参考](../reference/python.md) — 精确签名。
