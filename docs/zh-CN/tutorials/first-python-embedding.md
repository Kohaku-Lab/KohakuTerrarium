---
title: 在 Python 中嵌入
summary: 在你自己的 Python 代码里运行 Agent：带类型的轮次、自定义工具、引擎托管的生物、会话文件与恢复。
tags:
  - tutorials
  - python
  - embedding
---

# 第一次 Python 嵌入

**问题：**你想在自己的 Python 应用里运行一个生物 (creature)：给它派
活、观察它在干什么、留下记录、之后还能恢复。

**目标状态：**一个最小脚本：用 `Agent.build` 构建 Agent，用
`run` / `run_stream` 驱动带类型的轮次，用 `@kt.tool` 注入自定义工具，
在 `Terrarium` 里托管生物并带会话文件，用 `SessionReader` 把会话读回
来，最后恢复它。

**前置条件：**[第一个生物](first-creature.md)。包要以能
`import kohakuterrarium` 的方式安装好。

这个框架里的 Agent 不是配置，它是 Python 对象。配置只是描述；
`Agent.build(...)` 构造出一个归你所有的对象。心智模型见
[agent-as-python-object](../concepts/python-native/agent-as-python-object.md)。

## 第 1 步：可编辑安装

目标：在你的 venv 里能导入 `kohakuterrarium`。

在仓库根目录：

```bash
uv pip install -e .[dev]
```

`[dev]` 附加项会带上之后可能用到的测试辅助工具。

## 第 2 步：一个 Agent，一个轮次

目标：构建 Agent，驱动一个轮次，拿到带类型的结果。

`demo.py`：

```python
import asyncio

from kohakuterrarium import Agent


async def main() -> None:
    agent = await Agent.build("@kt-biome/creatures/general")
    await agent.start()
    try:
        result = await agent.run(
            "In one sentence, what is a creature in KohakuTerrarium?",
            timeout=300,
        )
        print(result.text)
        print(f"[status={result.status} {result.duration_s:.1f}s]")
    finally:
        await agent.stop()


asyncio.run(main())
```

运行：

```bash
python demo.py
```

注意三件事：

1. `Agent.build` 解析 `@kt-biome/...` 的方式和 CLI 一样，而且环境
   有问题时会**抛错**（`kt.errors.ConfigNotFoundError`、
   `LLMNotConfiguredError` 等），不会跑了半天什么都没产出。
2. `run()` 返回 `TurnResult`：`status`（`"ok"` / `"error"` /
   `"timeout"` / `"interrupted"`）、`text`、`error`、`tool_calls`、
   `usage`、`duration_s`。失败的轮次默认抛 `kt.errors.TurnError`；
   传 `raise_on_error=False` 则自己根据 `result.status` 分支。
3. `timeout=` 会真正中断轮次，“超时”之后不会有 token 继续烧。

## 第 3 步：流式输出

目标：文本边到边渲染，工具活动实时可见。

```python
import asyncio

from kohakuterrarium import Agent, Activity, TextChunk, TurnEnded


async def main() -> None:
    agent = await Agent.build("@kt-biome/creatures/general")
    await agent.start()
    try:
        async for event in agent.run_stream("Plan a tropical terrarium."):
            if isinstance(event, TextChunk):
                print(event.text, end="", flush=True)
            elif isinstance(event, Activity):
                print(f"\n[{event.kind}] {event.detail}")
            elif isinstance(event, TurnEnded):
                print(f"\n[done: {event.result.status}]")
    finally:
        await agent.stop()


asyncio.run(main())
```

`run_stream` 产出带类型的联合
`TextChunk | Activity | TurnEnded`，而且流中途绝不抛错：错误以
`Activity(kind="processing_error")` 的形式到达，并体现在最终结果里。

## 第 4 步：用普通函数给它一个工具

目标：用你自己的能力扩展 Agent，不碰配置文件。

```python
import asyncio

import kohakuterrarium as kt

INVENTORY = {"moss": 12, "fern": 3}


@kt.tool
def check_stock(item: str) -> str:
    """Look up how many units of an item are in stock."""
    count = INVENTORY.get(item.lower())
    return f"{item}: {count} in stock" if count is not None else f"{item}: not found"


async def main() -> None:
    agent = await kt.Agent.build(
        "@kt-biome/creatures/general",
        tools=[check_stock],
    )
    await agent.start()
    try:
        result = await agent.run("Do we have ferns in stock?")
        print(result.text)
        print(f"[tools used: {[t.detail for t in result.tool_calls]}]")
    finally:
        await agent.stop()


asyncio.run(main())
```

`@kt.tool` 从类型注解推导 schema、从 docstring 取描述；同步函数在
线程里跑，异步函数直接 await。也可以给运行中的 Agent 加能力
（`agent.add_tool(...)`、`await agent.add_plugin(...)`），系统提示词会
刷新，控制器真的能看到它们。

## 第 5 步：托管进引擎，带会话文件

目标：单生物工作目录 + 可恢复的会话文件，零持久化仪式。

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    async with Terrarium() as engine:
        clerk = await engine.add_creature(
            "@kt-biome/creatures/general",
            pwd="workdir",                        # the creature's cwd
            session="runs/clerk.kohakutr",        # minted + closed for you
        )
        result = await clerk.run("Summarize the files in this directory.")
        print(result.text)


asyncio.run(main())
```

引擎可以托管任意数量的生物
（[批处理写法](../guides/programmatic-usage.md#批处理的范式写法)见
[`examples/code/batch_grading.py`](../../../examples/code/batch_grading.py)，
在信号量限流下给每个提交文件夹跑一个生物）。离开 `async with` 块时，
所有生物停止、引擎创建的所有会话存储关闭。改用
`Terrarium(session_dir="runs/")` 则会自动持久化每张图。

## 第 6 步：把会话读回来

目标：离线检查发生过什么，且不碰文件的状态。

```python
from kohakuterrarium import SessionReader

with SessionReader("runs/clerk.kohakutr") as r:
    print(r.meta["session_id"], r.meta["status"])
    for turn in r.turns():
        tools = [tc["name"] for tc in turn.tool_calls]
        print(f"- {turn.user_text[:40]!r} -> {turn.assistant_text[:60]!r} {tools}")
```

`SessionReader` 是只读的（它经 `SessionStore.open_readonly` 打开），
检视永远不会更新 `last_active` 或改写 `status`。

## 第 7 步：恢复

目标：在新进程里接着聊。

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    async with await Terrarium.resume("runs/clerk.kohakutr") as engine:
        clerk = engine.list_creatures()[0]
        result = await clerk.run("Continue where you left off.")
        print(result.text)


asyncio.run(main())
```

`Terrarium.resume` 根据会话元数据里记录的配置路径重建拓扑，并重新
注入保存的对话。`engine.adopt_session(...)` 则是恢复进一个已经在跑
其他图的引擎。

## 你学到了什么

- `Agent.build` 是规范构造函数；它抛类型化的 `kt.errors.*` 异常，
  而不是静默降级。
- `run()` 返回 `TurnResult`；`run_stream()` 产出带类型的事件；
  `timeout=` 是动真格的中断。
- `@kt.tool` 把普通函数变成 Agent 工具；用 `tools=` /
  `add_tool` 注入。
- `Terrarium` 托管生物，提供单生物的 `pwd` 与 `session=` 持久化；
  `SessionReader` 读回文件；`Terrarium.resume` 接着跑。

## 接下来读什么

- [编程式用法指南](../guides/programmatic-usage.md)：Python 接口的
  任务导向参考，包括引擎事件、热插拔和验证。
- [组合代数](../guides/composition.md)：请求级流水线的
  `>>`、`&`、`|`、`*` 运算符。
- [会话指南](../guides/sessions.md)：关于 `.kohakutr` 文件的一切。
- [Python API 参考](../reference/python.md)：精确签名。
