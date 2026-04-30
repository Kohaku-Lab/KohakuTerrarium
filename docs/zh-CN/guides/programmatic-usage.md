---
title: 程序化使用
summary: 在你自己的 Python 代码里驱动 Studio、Terrarium、Creature 与底层 Agent。
tags:
  - guides
  - python
  - embedding
---

# 程序化使用

给想要在自己的 Python 代码里嵌入代理的读者。

Creature 不是配置文件本身——配置文件只是它的描述。运行起来的 Creature 是一个由 `Terrarium` 引擎托管的 async Python 对象。`Studio` 是引擎之上的管理 facade：catalog、identity、active sessions、saved sessions、attach policies 与 editor workflows。KohakuTerrarium 里的所有东西都是可以 call、可以 await 的。你的代码才是 orchestrator；代理是你叫它跑的 worker。

相关概念：[Studio](../concepts/studio.md)、[Terrarium](../concepts/multi-agent/terrarium.md)、[作为 Python 对象的代理](../concepts/python-native/agent-as-python-object.md)、[组合代数](../concepts/python-native/composition-algebra.md)。

## 入口

| 介面 | 什么时候用 |
|---|---|
| `Studio` | 管理 facade。用于 packages/catalog、settings/identity、active sessions、saved sessions、attach policy 和 editor workflows。 |
| `Terrarium` | 运行时引擎。增加 Creature、连接它们、订阅事件。同一个引擎处理独立与多 Creature 工作负载。 |
| `Creature` | 引擎里一只运行中的 Creature：`chat()`、`inject_input()`、`get_status()`。由 `Terrarium.add_creature` / `with_creature` 返回。 |
| `Agent` | 较底层：Creature 背后的 LLM 控制器。需要直接控制事件、触发器或输出 handler 时使用。 |

顶层 import 是稳定的：`from kohakuterrarium import Studio, Terrarium, Creature, EngineEvent, EventFilter`。

要在 Python 里做轻量 request pipeline、但不想手动管理长生命周期 terrarium graph 或 recipe，请看 [组合](composition.md)。

## `Studio` — 管理 facade

当你嵌入的是 CLI / dashboard 的职责时用 `Studio`：启动、列出和停止 sessions，恢复 `.kohakutr`，查看 packages，编辑 workspace，或暴露 attach streams。

```python
import asyncio
from kohakuterrarium import Studio

async def main():
    async with Studio() as studio:
        session = await studio.sessions.start_creature(
            "@kt-biome/creatures/general"
        )
        cid = session.creatures[0]["creature_id"]

        stream = await studio.sessions.chat.chat(
            session.session_id,
            cid,
            "Explain this project in one paragraph.",
        )
        async for chunk in stream:
            print(chunk, end="", flush=True)

asyncio.run(main())
```

常用构造 helper：

- `Studio()` — 创建带空引擎的 Studio。
- `await Studio.with_creature(config, *, pwd=None)` — 创建 Studio + 单 Creature session。
- `await Studio.from_recipe(recipe, *, pwd=None)` — 创建 Studio + recipe session。
- `await Studio.resume(store, *, pwd=None, llm_override=None)` — 把保存的 session 恢复进新 Studio。
- `await studio.shutdown()` — 停掉底层引擎；`async with` 会自动调用。

重要 namespace：

- `studio.sessions` — active graph/session lifecycle 与 per-creature chat/control/state helpers。
- `studio.catalog` — packages、built-ins、workspace creature/module listing、introspection。
- `studio.identity` — LLM profiles/backends、API keys、Codex auth、MCP、UI preferences。
- `studio.persistence` — saved-session list/resolve/resume/fork/history/viewer/export/delete。
- `studio.attach` — chat、channel observer、trace/logs、files、pty 的 live attach policy。
- `studio.editors` — workspace creature/module scaffolding 与写入。

更多 task-oriented 示例见 [Studio 指南](studio.md)。

## `Terrarium` — 引擎

每个进程一个引擎，托管所有 Creature。独立代理是 1-creature graph；recipe 是带 channel 的 connected graph。

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

### Recipe（多 Creature）

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    engine = await Terrarium.from_recipe("@kt-biome/terrariums/swe_team")
    try:
        swe = engine["swe"]
        async for chunk in swe.chat("Fix the off-by-one in pagination.py"):
            print(chunk, end="", flush=True)
    finally:
        await engine.shutdown()

asyncio.run(main())
```

Recipe 描述「加入这些 Creature、宣告这些 channel、接这些 listen/send edges」。`from_recipe()` 会走完它，把每只 Creature 都放进同一个 graph 并启动它们。

### Async context manager

```python
async with Terrarium() as engine:
    alice = await engine.add_creature("@kt-biome/creatures/general")
    bob = await engine.add_creature("@kt-biome/creatures/general")
    await engine.connect(alice, bob, channel="alice_to_bob")
    # ...
# 离开时自动 shutdown()
```

### 热插拔

拓扑可以在运行时改变。跨 graph 的 `connect()` 会合并两个 graph（environment 取联集，挂着的 session store 合并成一份）。`disconnect()` 可能把 graph 拆开（parent session 复制到两边）。

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")

    result = await engine.connect(a, b, channel="a_to_b")
    # result.delta_kind == "merge"

    await engine.disconnect(a, b, channel="a_to_b")
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
```

引擎里所有可观察的事——文字 chunk、channel message、拓扑变更、session fork、错误——都以 `EngineEvent` 形式浮现。`EventFilter` 用 AND 把 kinds、creature ID、graph ID、channel 名组合起来。

### 关键方法

- `await Terrarium.with_creature(config)` — 引擎 + 一只 Creature。
- `await Terrarium.from_recipe(recipe)` — 引擎 + 套用一份 recipe。
- `await Terrarium.resume(store, *, pwd=None, llm_override=None)` — 建引擎并采用 saved session。
- `await engine.adopt_session(store, *, pwd=None, llm_override=None)` — 恢复进既有引擎并返回 graph id。
- `await engine.add_creature(config, *, graph=None, start=True)` — 加进既有 graph 或开一个新的 singleton graph。
- `await engine.remove_creature(creature)` — 停掉并移除；可能拆 graph。
- `await engine.add_channel(graph, name, kind=...)` — 宣告 channel。
- `await engine.connect(a, b, channel=...)` — 接 `a → b`；需要时合并 graph。
- `await engine.disconnect(a, b, channel=...)` — 拆掉一条或全部边；可能拆 graph。
- `await engine.wire_output(creature, sink)` / `await engine.unwire_output(creature, sink_id)` — 第二组输出 sink。
- `engine[id]`、`id in engine`、`for c in engine`、`len(engine)` — Pythonic accessor。
- `engine.list_graphs()` / `engine.get_graph(graph_id)` — graph 检视。
- `engine.status()` / `engine.status(creature)` — 整体或单只 Creature 的状态 dict。
- `await engine.shutdown()` — 停掉每只 Creature；幂等。

运行时机制用 `Terrarium`；如果还需要 catalog、settings、saved-session、attach 或 editor 管理，用 `Studio`。

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
- `await agent.run()` — 内置主回圈（从输入拉事件、派发触发器、跑控制器）。
- `await agent.inject_input(content, source="programmatic")` — 绕过输入模块直接推输入。
- `await agent.inject_event(TriggerEvent(...))` — 推任何事件。
- `agent.interrupt()` — 中止当前处理周期（非阻塞）。
- `agent.switch_model(profile_name)` — 执行期换 LLM。
- `agent.llm_identifier()` — 读取规范化的 `provider/name[@variations]` 标识。
- `agent.set_output_handler(fn, replace_default=False)` — 新增或取代输出 sink。
- `await agent.add_trigger(trigger)` / `await agent.remove_trigger(id)` — 执行期管触发器。

属性：

- `agent.is_running: bool`
- `agent.tools: list[str]`、`agent.subagents: list[str]`
- `agent.conversation_history: list[dict]`

## `Creature` — 串流聊天

`Creature.chat(message)` 会在控制器串流时 yield 文字 chunk。工具活动与子代理事件仍然通过底层 output/event path 表面化；`Creature` 专注在简单文字流与状态句柄。

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    engine, creature = await Terrarium.with_creature("@kt-biome/creatures/swe")
    try:
        async for chunk in creature.chat("What does this do?"):
            print(chunk, end="")
        print()
    finally:
        await engine.shutdown()

asyncio.run(main())
```

当你只想推输入、不消费输出时用 `Creature.inject_input(message, source=...)`；需要 model、tools、sub-agents、graph id、channels、working directory 状态时用 `Creature.get_status()`。

## 接输出

`set_output_handler` 让你挂任何 callable：

```python
def handle(text: str) -> None:
    my_logger.info(text)

agent.set_output_handler(handle, replace_default=True)
```

多个 sink（TTS、Discord、文件）的话，在 YAML 设置 `named_outputs`，代理会自动路由。

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

`type` 可以是任何控制器接得住的字符串——`user_input`、`idle`、`timer`、`channel_message`、`context_update`、`monitor`，或你自己定义的。见 [reference/python 参考](../reference/python.md)。

## 多租户 server

HTTP API 把 `Studio` 当成共享 `Terrarium` 引擎之上的管理 facade。API route 通过 Studio namespace 启动 sessions、与 creatures 聊天、查看 settings、恢复 saved sessions，而不是重复实现这些策略。你自己的 server 也建议用同样形状：

```python
from kohakuterrarium import Studio

studio = Studio()
session = await studio.sessions.start_creature(
    "@kt-biome/creatures/swe",
    pwd="/srv/workspaces/project-a",
)
cid = session.creatures[0]["creature_id"]

stream = await studio.sessions.chat.chat(session.session_id, cid, "Hi")
async for chunk in stream:
    print(chunk, end="")

print(studio.engine.status(cid))
await studio.sessions.stop(session.session_id)
```

FastAPI handler 会通过 dependency helper 取得每个进程的 `Studio` / `Terrarium` 对象。Route handler 应把 catalog、identity、active-session、persistence、attach 与 editor policy 交给 Studio namespace。

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

或者在适合时使用 `Terrarium`、`Studio` 或 `compose.agent()` 作为 async context manager。

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

简单情境下 `Terrarium(session_dir=...)` 会自动处理——把 `session_dir=` 传给引擎，它就会在 `attach_session` 时挂上每个 graph 的 store。

如果 agent 会生成 binary artifacts（例如 provider-native images），请在运行前 attach session store，这样 artifacts 才会与 session file 一起持久化到 `<session>.artifacts/`。

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

- **`await agent.run()` 一直不返回。** `run()` 是完整的事件回圈；输入模块关掉（例如 CLI 收到 EOF）或终止条件触发时才会结束。要做 one-shot 互动请改用 `inject_input` + `stop`。
- **输出 handler 没有被调用。** 如果你不想连 stdout 一起出，记得将 `replace_default=True`；并确认代理在 inject 之前已经 start。
- **热插拔的 Creature 收不到消息。** 用 `engine.connect(sender, receiver, channel=...)`——引擎会处理 channel 注册和 trigger 注入。只 `add_creature` 会把 Creature 放进一个没有任何入站 channel 的 singleton graph。
- **`Creature.chat` 看起来卡住。** 另一个调用者可能正在使用同一只 Creature；请按 Creature 串行化访问，或为独立调用者启动分开的 session / Creature。

## 延伸阅读

- [组合](composition.md) — Python 端的多代理管线。
- [自定义模块指南](custom-modules.md) — 自己写工具/输入/输出并接上来。
- [Reference / Python API 参考](../reference/python.md) — 完整签名。
- [examples/code/](../../examples/code/) — 各种 pattern 的可执行示例。
