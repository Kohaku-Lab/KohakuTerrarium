---
title: 程序化使用
summary: 在你自己的 Python 代码里驱动 Terrarium 引擎、Creature、Agent。
tags:
 - guides
 - python
 - embedding
---

# 程序化使用

给想要在自己的 Python 代码里嵌入代理的读者。

Creature 不是配置文件本身 — 配置文件只是它的描述。运行起来的 Creature 是一个由 `Terrarium` 引擎托管的 async Python 物件。KohakuTerrarium 里的所有东西都是可以 call、可以 await 的。你的代码才是那个 orchestrator；代理是你叫它跑的 worker。

相关概念：[作为 Python 物件的代理](../concepts/python-native/agent-as-python-object.md)、[组合代数](../concepts/python-native/composition-algebra.md)。

## 入口

| 介面 | 什么时候用 |
|---|---|
| `Terrarium` | 单一的运行时引擎。增加 Creature、连接它们、订阅事件。同一个引擎处理独立 Agent 和多 Agent 工作负载。 |
| `Creature` | 引擎里一只运行中的 Creature — `chat()`、`inject_input()`、`get_status()`。由 `Terrarium.add_creature` / `with_creature` 返回。 |
| `Agent` | 较底层：Creature 背后的 LLM 控制器。需要直接控制事件、触发器或输出 handler 时使用。 |
| `AgentSession` | 旧版串流 wrapper。仍可用，但 `Creature.chat()` 是新的等价物。 |

顶层 import 是稳定的：`from kohakuterrarium import Terrarium, Creature, EngineEvent, EventFilter`。

要在 Python 里做不靠引擎的多代理管线，看 [组合代数开发指南](composition.md)。

## `Terrarium` — 引擎

每个进程一个引擎，托管所有 Creature。独立 Agent 是 1-creature graph；recipe 是用频道连起来的 connected graph。

### 独立 Creature

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    engine, alice = await Terrarium.with_creature("@kt-biome/creatures/swe")
    try:
        async for chunk in alice.chat("Explain what this codebase does."):
            print(chunk, end="", flush=True)
    finally:
        await engine.shutdown()

asyncio.run(main())
```

`Terrarium.with_creature(config)` 建出引擎并把一只 Creature 放进 1-creature graph。返回的 `Creature` 暴露 `chat()`、`inject_input()`、`is_running`、`graph_id`、`get_status()`。

### Recipe (多 Creature)

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    engine = await Terrarium.from_recipe("@kt-biome/terrariums/swe_team")
    try:
        # 用 id 拿到引擎里某只 Creature
        swe = engine["swe"]
        async for chunk in swe.chat("Fix the off-by-one in pagination.py"):
            print(chunk, end="", flush=True)
    finally:
        await engine.shutdown()

asyncio.run(main())
```

Recipe 描述「加这些 Creature、宣告这些频道、接这些 listen / send 边」。`from_recipe()` 会走完它，把每只 Creature 都放进同一个 graph 并启动它们。

### Async context manager

```python
async with Terrarium() as engine:
    alice = await engine.add_creature("@kt-biome/creatures/general")
    bob   = await engine.add_creature("@kt-biome/creatures/general")
    await engine.connect(alice, bob, channel="alice_to_bob")
    # ...
# 离开时自动 shutdown()
```

### 热插拔

拓扑可以在运行时改变。跨 graph 的 `connect()` 会合并两个 graph (environment 取联集，挂著的 session store 合并成一份)。`disconnect()` 可能把 graph 拆开 (parent session 复制到两边)。

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")
    # a 和 b 各自处于独立的 graph

    result = await engine.connect(a, b, channel="a_to_b")
    # result.delta_kind == "merge" — a 和 b 现在共享同一个 graph、
    # 同一个 environment、同一个 session store

    await engine.disconnect(a, b, channel="a_to_b")
    # 拆回两个 graph；每边都带著合并後的历史
```

参考 [`examples/code/terrarium_hotplug.py`](../../examples/code/terrarium_hotplug.py)。

### 订阅引擎事件

```python
from kohakuterrarium import EventFilter, EventKind

async with Terrarium() as engine:
    async def watch():
        async for ev in engine.subscribe(
            EventFilter(kinds={EventKind.TOPOLOGY_CHANGED, EventKind.CREATURE_STARTED})
        ):
            print(ev.kind.value, ev.creature_id, ev.payload)
    asyncio.create_task(watch())
    # ... 驱动引擎
```

引擎里所有可观察的事 — 文字 chunk、频道消息、拓扑变更、session fork、错误 — 都以 `EngineEvent` 形式浮现。`EventFilter` 用 AND 把 kinds、creature ID、graph ID、频道名组合起来。

### 关键方法

- `await Terrarium.with_creature(config)` — 引擎 + 一只 Creature。
- `await Terrarium.from_recipe(recipe)` — 引擎 + 套用一份 recipe。
- `await Terrarium.resume(store)` — *尚未实现*。
- `await engine.add_creature(config, *, graph=None, start=True)` — 加进既有 graph 或开一个新的 singleton graph。
- `await engine.remove_creature(creature)` — 停掉并移除；可能拆 graph。
- `await engine.add_channel(graph, name, kind=...)` — 宣告频道。
- `await engine.connect(a, b, channel=...)` — 接 `a → b`；需要时合并 graph。
- `await engine.disconnect(a, b, channel=...)` — 拆掉一条或全部边；可能拆 graph。
- `await engine.wire_output(creature, sink)` / `await engine.unwire_output(creature, sink_id)` — 第二组输出 sink。
- `engine[id]`、`id in engine`、`for c in engine`、`len(engine)` — Pythonic accessor。
- `engine.list_graphs()` / `engine.get_graph(graph_id)` — graph 检视。
- `engine.status()` / `engine.status(creature)` — 整体或单只 Creature 的状态 dict。
- `await engine.shutdown()` — 停掉每只 Creature；幂等。

旧版 `TerrariumRuntime` 与 `KohakuManager` 在过渡期间还留在硬盘上、仍然能跑。新代码请用 `Terrarium`。

## `Agent` — 完整控制权

```python
import asyncio
from kohakuterrarium.core.agent import Agent

async def main():
    agent = Agent.from_path("@kt-biome/creatures/swe")
    agent.set_output_handler(
        lambda text: print(text, end=""),
        replace_default=True,
    )
    await agent.start()
    await agent.inject_input("Explain what this codebase does.")
    await agent.stop()

asyncio.run(main())
```

关键方法：

- `Agent.from_path(path, *, input_module=..., output_module=..., session=..., environment=..., llm_override=..., pwd=...)` — 从 config 目录或 `@pkg/...` 参照建出代理。
- `await agent.start()` / `await agent.stop()` — lifecycle。
- `await agent.run()` — 内置主回圈 (从输入拉事件、派发触发器、跑控制器)。
- `await agent.inject_input(content, source="programmatic")` — 绕过输入模块直接推输入。
- `await agent.inject_event(TriggerEvent(...))` — 推任何事件。
- `agent.interrupt()` — 中止当前处理周期 (非阻塞)。
- `agent.switch_model(profile_name)` — 执行期换 LLM。
- `agent.set_output_handler(fn, replace_default=False)` — 新增或取代输出 sink。
- `await agent.add_trigger(trigger)` / `await agent.remove_trigger(id)` — 执行期管触发器。

属性：

- `agent.is_running: bool`
- `agent.tools: list[str]`、`agent.subagents: list[str]`
- `agent.conversation_history: list[dict]`

## `AgentSession` — 串流式聊天

```python
import asyncio
from kohakuterrarium.serving.agent_session import AgentSession

async def main():
    session = await AgentSession.from_path("@kt-biome/creatures/swe")
    await session.start()
    async for chunk in session.chat("What does this do?"):
        print(chunk, end="")
    print()
    await session.stop()

asyncio.run(main())
```

`chat(message)` 会在控制器串流时 yield 文字 chunk。工具活动与子代理事件通过输出模块的 activity callback 表面化 — `AgentSession` 专注在文字流；要更丰富的事件请用 `Agent` 配自定义输出模块。

Builder：`AgentSession.from_path(...)`、`from_config(AgentConfig)`、`from_agent(pre_built_agent)`。

## 接输出

`set_output_handler` 让你挂任何 callable：

```python
def handle(text: str) -> None:
    my_logger.info(text)

agent.set_output_handler(handle, replace_default=True)
```

多个 sink (TTS、Discord、文件) 的话，在 YAML 设置 `named_outputs`，代理会自动路由。

## 事件层控制

```python
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event

await agent.inject_event(create_user_input_event("Hi", source="slack"))
await agent.inject_event(TriggerEvent(
    type="context_update",
    content="User just navigated to page /settings.",
    context={"source": "frontend"},
))
```

`type` 可以是任何控制器接得住的字符串 — `user_input`、`idle`、`timer`、`channel_message`、`context_update`、`monitor`，或你自己定义的。见 [reference/python 参考](../reference/python.md)。

## 多租户 server

HTTP API 把 `Terrarium` 引擎当成单进程的 singleton — 每个请求都通过引擎的 Pythonic accessor 以 ID 加、移、聊：

```python
from kohakuterrarium import Terrarium

engine = Terrarium(session_dir="/var/kt/sessions")

alice = await engine.add_creature("@kt-biome/creatures/swe", creature_id="alice")
async for chunk in alice.chat("Hi"):
    print(chunk, end="")

print(engine.status("alice"))
await engine.stop("alice")
```

FastAPI handler 这一边，`kohakuterrarium.api.deps.get_engine()` 会返回每个进程的 singleton；旧版的 `get_manager()` (`KohakuManager`) 在所有 route 都切过去之前还接著。

## 干净地停下来

永远把 `start()` 跟 `stop()` 配对：

```python
agent = Agent.from_path("...")
try:
    await agent.start()
    await agent.inject_input("...")
finally:
    await agent.stop()
```

或用 `AgentSession` / `compose.agent()`，它们是 async context manager。

Interrupt 在任何 asyncio task 里都安全：

```python
agent.interrupt()           # 非阻塞
```

控制器在 LLM 串流步骤之间会检查 interrupt 旗标。

## 自定义 session / environment

```python
from kohakuterrarium.core.session import Session
from kohakuterrarium.core.environment import Environment

env = Environment(env_id="my-app")
session = env.get_session("my-agent")
session.extra["db"] = my_db_connection

agent = Agent.from_path("...", session=session, environment=env)
```

放进 `session.extra` 的东西，工具可以通过 `ToolContext.session` 读到。

## 挂 session 持久化

```python
from kohakuterrarium.session.store import SessionStore

store = SessionStore("/tmp/my-session.kohakutr")
store.init_meta(
    session_id="s1",
    config_type="agent",
    config_path="path/to/creature",
    pwd="/tmp",
    agents=["my-agent"],
)
agent.attach_session_store(store)
```

简单情境下 `Terrarium(session_dir=...)` 会自动处理 — 把 `session_dir=` 传给引擎，它就会在 `attach_session` 时挂上每个 graph 的 store。

## 测试

```python
from kohakuterrarium.testing.agent import TestAgentBuilder

env = (
    TestAgentBuilder()
    .with_llm_script([
        "Let me check. [/bash]@@command=ls\n[bash/]",
        "Done.",
    ])
    .with_builtin_tools(["bash"])
    .with_system_prompt("You are helpful.")
    .build()
)

await env.inject("List files.")
assert "Done" in env.output.all_text
assert env.llm.call_count == 2
```

`ScriptedLLM` 是决定性的；`OutputRecorder` 会抓 chunk/write/activity 供 assert。

## 疑难排解

- **`await agent.run()` 一直不返回**。 `run()` 是完整的事件回圈；输入模块关掉 (例如 CLI 收到 EOF) 或终止条件触发时才会结束。要做 one-shot 互动请改用 `inject_input` + `stop`。
- **输出 handler 没有被调用**。 如果你不想连 stdout 一起出，记得将 `replace_default=True`；并确认代理在 inject 之前已经 start。
- **热插拔的 Creature 收不到消息**。 用 `engine.connect(sender, receiver, channel=...)` — 引擎会处理频道注册和触发器注入。只 `add_creature` 会把 Creature 放进一个没有任何入站频道的 singleton graph。
- **`AgentSession.chat` 卡住**。 另一个调用者正在使用这个代理；session 会串行化输入。每个调用者配一个 `AgentSession`。

## 延伸阅读

- [组合代数开发指南](composition.md) — 纯 Python 端的多代理管线。
- [自定义模块指南](custom-modules.md) — 自己写工具/输入/输出并接上来。
- [Reference / Python API 参考](../reference/python.md) — 完整签名。
- [examples/code/](../../examples/code/) — 各种 pattern 的可执行示例。
