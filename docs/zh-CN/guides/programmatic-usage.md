---
title: 编程式用法
summary: 在自己的 Python 代码里驱动 Agent、Terrarium 和 Creature：带类型的轮次、严格的错误、引擎持有的会话。
tags:
  - guides
  - python
  - embedding
---

# 编程式用法

写给想把 Agent 嵌进自己 Python 代码的读者。

生物 (creature) 不是配置文件，配置只是它的描述。运行中的 Agent 是一个
异步 Python 对象，编程接口围绕三个承诺构建：

1. **带类型的轮次。**`run()` 返回 `TurnResult`（状态、文本、工具调用、
   用量、耗时）；`run_stream()` 实时产出带类型的事件。
2. **严格的错误。**编程式构造函数和轮次会**抛出**类型化的
   `kt.errors.*` 异常，而不是静默降级：提供商挂了就是异常，
   不是一个干净的空回复。
3. **引擎持有的会话。**持久化是一个关键字参数
   （`session=`、`Terrarium(session_dir=...)`），不是一套仪式。

精确签名见 [reference/python](../reference/python.md)。

## 入口

| 接口 | 适用场景 |
|---|---|
| `Agent` | 单个 Agent，不需要引擎特性。`await Agent.build(...)` 然后 `run` / `run_stream`。 |
| `Terrarium` | 运行时引擎。单生物工作目录、会话文件、频道、热插拔、事件。只要运行不止一个 Agent（或者一个想要持久化的 Agent）就该用它。 |
| `Creature` | 引擎中运行的 Agent：`run`、`run_stream`、`attach`、`get_status`。由 `add_creature` / `with_creature` 返回。 |
| `Studio` | 引擎之上的管理门面（目录、已保存会话、编辑器）。见 [Studio 指南](studio.md)。 |
| `compose` | 请求级流水线（`>>`、`&`、`\|`、`*`），见[组合](composition.md)。 |

顶层导入：`from kohakuterrarium import Agent, Terrarium,
Creature, TurnResult, TextChunk, Activity, TurnEnded, SessionReader,
tool, errors, validate`。

## 一个 Agent，一个轮次

```python
import asyncio
from kohakuterrarium import Agent, TextChunk, TurnEnded

async def main():
    agent = await Agent.build("@kt-biome/creatures/general")
    await agent.start()
    try:
        # Buffered: one TurnResult with status / text / usage.
        result = await agent.run("What is a terrarium?", timeout=300)
        print(result.text)
        if result.usage:
            print(f"[{result.usage.get('total_tokens', '?')} tokens]")

        # Streamed: typed events as they happen.
        async for event in agent.run_stream("How would you build one?"):
            if isinstance(event, TextChunk):
                print(event.text, end="", flush=True)
            elif isinstance(event, TurnEnded):
                print(f"\n[turn status: {event.result.status}]")
    finally:
        await agent.stop()

asyncio.run(main())
```

（完整脚本：[`examples/code/programmatic_chat.py`](../../../examples/code/programmatic_chat.py)。）

`Agent.build` 接受配置文件夹路径、`@pkg/...` 包引用，或已加载的
`AgentConfig`。它返回的 Agent **尚未启动**：`await agent.start()`
一定要和 `await agent.stop()` 成对出现（`Agent` 上没有 `async with`）。

`agent.run_forever()` 是传统的自治主循环（输入模块 + 触发器驱动
Agent，直到输入退出），`kt run` 用的就是它。脚本几乎总是应该用
`run` / `run_stream`。

## 什么时候抛错、抛什么

编程接口默认严格：

- **构造**（`Agent.build`、`engine.add_creature`）对缺失的配置或未安装
  的包抛 `kt.errors.ConfigNotFoundError`，对解析不出的模型抛
  `LLMNotConfiguredError`，对未知工具 / 损坏插件也会抛错。交互式前端
  传 `strict=False` 改为降级。
- **轮次**失败时抛 `TurnError`，超时抛 `TurnTimeoutError`。
  `timeout=` 会真正**中断**轮次（不会丢下一个还在跑的 LLM 调用）。
  传 `raise_on_error=False` 则总能拿回 `TurnResult`，自己根据
  `result.status`（`"ok"` / `"error"` / `"timeout"` /
  `"interrupted"`）分支，这正是批处理任务想要的形态。
- `run_stream` 在迭代过程中绝不抛错：错误以
  `Activity(kind="processing_error")` 事件的形式到达，并体现在最后的
  `TurnEnded(result)` 里。

```python
from kohakuterrarium import errors

try:
    result = await agent.run("Grade this submission.", timeout=1800)
except errors.TurnTimeoutError:
    print("over budget: turn was interrupted")
except errors.TurnError as e:
    print(f"turn failed: {e}")
```

长任务开跑前先用
[`kt.validate`](../reference/python.md#validate) 验证环境：
`validate.config`、`validate.llm`、`validate.creature`（完整干跑构建）、
`await validate.ping`（一次真实往返）。CLI 里的对应物是 `kt doctor`。

## 带上你自己的工具、插件和 LLM

`@kt.tool` 把一个普通函数变成 Agent 工具：schema 来自类型注解，
描述来自 docstring。同步函数在线程里跑；异步函数直接 await。

```python
import kohakuterrarium as kt

@kt.tool
def check_stock(item: str) -> str:
    """Look up how many units of an item are in stock."""
    return lookup(item)

agent = await kt.Agent.build(
    "@kt-biome/creatures/general",
    llm="default",                 # profile name; a typo raises here
    tools=[check_stock],           # instances, in the initial prompt
    plugins=[MyTracePlugin()],
)
```

构造之后还可以扩展运行中的 Agent，每次调用都会刷新系统提示词，
控制器真的能看到变化：

```python
agent.add_tool(other_tool)
await agent.add_plugin(plugin)     # on_load fires even post-start
agent.add_subagent(subagent_cfg)
```

`llm=` 在所有地方（`Agent.build`、`engine.add_creature`、
`compose.agent`）接受四种形态：

- `None`：从配置解析；
- 选择器字符串，即 profile / preset 名称或
  `provider/model[@variations]`；
- 一个 `LLMProfile` 实例；
- 一个提供商实例，例如测试用的 `ScriptedLLM`。

`io=` 决定配置里的 I/O 启动多少：`"config"`（按声明）、`"none"`
（禁用输入）、`"headless"`（禁用输入且静音默认输出，批处理默认值，
这样 N 个并发 Agent 不会在控制台里交错刷屏）。

## 引擎：`Terrarium`

每个进程一个引擎，托管所有生物；单独运行的 Agent 就是一张单生物的图。
当你需要单生物工作目录、会话文件、频道或运行时拓扑时，就用引擎。

### 批处理的范式写法

一个共享引擎，每个工作文件夹一个生物，各有自己的 `pwd` 和会话文件
（[`examples/code/batch_grading.py`](../../../examples/code/batch_grading.py)）：

```python
import asyncio
from kohakuterrarium import Terrarium

async def grade_one(engine, folder, gate):
    async with gate:
        creature = await engine.add_creature(
            "@kt-biome/creatures/general",
            llm="default",
            pwd=folder,                                   # no global os.chdir
            session=folder / "scoring_session.kohakutr",  # resumable later
        )
        try:
            return folder.name, await creature.run(
                PROMPT, timeout=1800, raise_on_error=False
            )
        finally:
            await engine.remove_creature(creature)

async def main():
    gate = asyncio.Semaphore(8)
    async with Terrarium() as engine:
        results = await asyncio.gather(*(grade_one(engine, d, gate) for d in folders))
    for name, r in results:
        print(name, r.status, r.duration_s, (r.usage or {}).get("total_tokens"))
```

### 配方

```python
from kohakuterrarium import Terrarium

async with await Terrarium.from_recipe("@kt-biome/terrariums/swe_team") as engine:
    swe = engine["swe"]
    result = await swe.run("Fix the off-by-one in pagination.py")
    print(result.text)
```

配方描述的是“添加这些生物、声明这些频道、接好这些监听/发送边”。
`from_recipe` 把所有生物落进一张图并启动它们。给 `apply_recipe` 加
`session=`（或用 `session_dir=` 构建引擎）即可持久化整张图。

### 热插拔与拓扑

拓扑可以在运行时改变。跨图 `connect()` 会自动合并两张图（环境取并
集，会话存储合并）；`disconnect()` / `remove_creature()` 可能触发自动
拆分。所有图频道都是广播：每个监听者都会收到每条消息。

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")

    result = await engine.connect(a, b, channel="a_to_b")
    # result.delta_kind == "merge": one graph, one environment

    d = await engine.disconnect(a, b, channel="a_to_b")
    # d.delta_kind == "split": two graphs again, history copied to each
```

（完整脚本：[`examples/code/terrarium_hotplug.py`](../../../examples/code/terrarium_hotplug.py)。）

引擎为图的活动状态提供公开访问器，不需要去抠私有 dict：

```python
from kohakuterrarium.core.channel import ChannelMessage

graph_id = engine.list_graphs()[0].graph_id
env = engine.environment(graph_id)          # live Environment
tasks = engine.channel(graph_id, "tasks")   # live broadcast channel or None
if tasks is not None:
    await tasks.send(ChannelMessage(sender="user", content="Fix the bug"))
```

### 观察引擎事件

引擎总线承载的是**结构**事件（生物添加 / 启动 / 停止、拓扑变化、
频道消息、连线）；单个生物的文本和工具活动走轮次接口
（`run_stream` / `attach`）。

```python
from kohakuterrarium import EventFilter, EventKind

async def watch(engine):
    async for ev in engine.subscribe(
        EventFilter(kinds={EventKind.TOPOLOGY_CHANGED, EventKind.CREATURE_STARTED})
    ):
        print(ev.kind.value, ev.creature_id, ev.payload)
```

订阅者在调用 `subscribe()` 那一刻即注册，第一次 `await` 之前发出的
事件会被缓冲，“先订阅、再触发”的写法不会丢第一个事件。
`engine.shutdown()` 会终止存活的订阅者。

## `Creature`：运行中的句柄

`Creature` 镜像了 Agent 的轮次接口，并补上引擎侧的上下文：

- `await creature.run(content, timeout=..., raise_on_error=...)` → `TurnResult`
- `creature.run_stream(content)` → 带类型的事件
- `creature.attach()`：**非破坏性观察者**：一个异步上下文管理器，
  流式输出该生物发出的所有带类型事件，包括带外轮次（触发器、频道
  消息）。支持多消费者；默认输出和会话存储照常收到一切。
- `await creature.chat(message)`：纯文本语法糖；新代码请用带类型的
  驱动方法。
- `creature.status`：`"not_started"` / `"idle"` / `"busy"` /
  `"stopped"` / `"error"`；`creature.get_status()` 返回完整 dict。

```python
async with creature.attach() as stream:
    async for ev in stream:
        log(ev)          # tool starts, text, errors: everything
```

## 在代码里使用会话

持久化由引擎持有（旧的 `SessionStore` + `init_meta` +
`attach_session` 仪式已经不存在了）：

```python
# Autosession: every graph gets runs/<graph_id>.kohakutr automatically.
engine = Terrarium(session_dir="runs/")

# Or per creature: exact file, True (default dir), False (off), or a store.
c = await engine.add_creature("@kt-biome/creatures/general",
                              session="runs/student-42.kohakutr")

# Resume later: fresh engine or into a running one.
engine2 = await Terrarium.resume("runs/student-42.kohakutr")
graph_id = await engine.adopt_session("runs/other.kohakutr")
```

`engine.shutdown()` 会关闭引擎创建的所有存储。已完成的文件用
`SessionReader` 读取（元数据、事件、重组轮次、搜索），见
[会话](sessions.md)。

## 测试你的集成

直接注入 `ScriptedLLM`，不需要 monkeypatch：

```python
import kohakuterrarium as kt
from kohakuterrarium.testing.llm import ScriptedLLM

agent = await kt.Agent.build(cfg, llm=ScriptedLLM(["Hello!"]), io="headless")
await agent.start()
result = await agent.run("hi")
assert result.text == "Hello!"
assert agent.llm.call_count == 1
await agent.stop()
```

`engine.add_creature(path, llm=ScriptedLLM([...]))` 同理。

## 干净地停下来

- `Agent`：在 `try/finally` 里成对调用 `start()` / `stop()`。
- `Terrarium`：用 `async with`，退出时跑 `shutdown()`，停止所有生物，
  关闭引擎创建的所有会话存储。
- `agent.interrupt()` / `creature.agent.interrupt()` 可以从任意 asyncio
  任务取消当前轮次（非阻塞）。

## 故障排查

- **`await agent.run_forever()` 一直不返回。**它是自治主循环；只有
  输入模块关闭或终止条件触发时才退出。一次性交互请用
  `run` / `run_stream`。
- **第一次调用就 `TurnError: turn failed`。**提供商调用失败了，先
  跑 `kt.validate.llm("<selector>")` 和
  `await kt.validate.ping(...)`，再怀疑自己的代码。
- **热插进来的生物收不到消息。**用
  `engine.connect(sender, receiver, channel=...)`；只调
  `add_creature` 得到的是一张没有任何入站频道的单生物图。
- **同一个 Agent 上并发两个 `run()`。**轮次在 Agent 的处理锁上
  串行化；第二个 `run` 会等第一个。要并行就用多个生物
  （批处理写法）。
- **N 个并发 Agent 把控制台刷花了。**传 `io="headless"`，
  配置里的默认 stdout 输出会被静音；文本改从
  `run` / `run_stream` / 会话存储消费。

## 另请参阅

- [组合](composition.md)：请求级流水线。
- [会话](sessions.md)：持久化、恢复、`SessionReader`。
- [包](packages.md)：`@pkg/...` 引用与 `packages.ensure`。
- [参考 / Python API](../reference/python.md)：精确签名。
- [`examples/code/`](../../../examples/code/)：各个模式的可运行脚本
  （`batch_grading.py` 是批处理任务的范例）。
