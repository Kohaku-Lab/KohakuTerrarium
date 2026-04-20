---
title: 程序化使用
summary: 在你自己的 Python 代码里驱动 Agent、AgentSession、TerrariumRuntime、KohakuManager。
tags:
 - guides
 - python
 - embedding
---

# 程序化使用

给想要在自己的 Python 代码里嵌入代理的读者。

Creature不是配置文件本身 — 配置文件只是它的描述。运行来的Creature是一个 async Python 物件：`Agent`。KohakuTerrarium 里的所有东西 (包含Terrarium 与 session) 都是可以 call、可以 await 的。你的代码才是那个 orchestrator；代理是你叫它跑的 worker。

相关概念：[作为 Python 物件的代理](../concepts/python-native/agent-as-python-object.md)、[组合代数](../concepts/python-native/composition-algebra.md)。

## 四个入口

| 介面 | 什么时候用 |
|---|---|
| `Agent` | 你想要完整控制权：注入事件、接自定义输出、自己管理 lifecycle。 |
| `AgentSession` | 串流式聊天的 wrapper：注入输入、逐 chunk 走过输出。拿来做 bot 或 web UI 都合适。 |
| `TerrariumRuntime` | 有一份Terrarium config，想把它运行来。 |
| `KohakuManager` | 多租户 server：多个代理/Terrarium以 ID 管理，与传输层无关。 |

要在 Python 里做多代理管线而不建Terrarium，看 [组合代数开发指南](composition.md)。

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

## 从 code 跑Terrarium

```python
import asyncio
from kohakuterrarium.terrarium.runtime import TerrariumRuntime
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.core.channel import ChannelMessage

async def main():
    config = load_terrarium_config("@kt-biome/terrariums/swe_team")
    runtime = TerrariumRuntime(config)
    await runtime.start()

    tasks = runtime.environment.shared_channels.get("tasks")
    await tasks.send(ChannelMessage(sender="user", content="Fix the auth bug."))

    await runtime.run()
    await runtime.stop()

asyncio.run(main())
```

Runtime 方法：`start`、`stop`、`run`、`add_creature`、`remove_creature`、`add_channel`、`wire_channel`。`environment` 里有 `shared_channels` (一个 `ChannelRegistry`)，所有Creature都看得到；每只 Creature有自己私有的 `Session`。

## `KohakuManager` — 多租户

HTTP API、web app、以及任何需要「用 ID 管多个代理」的代码都用它：

```python
from kohakuterrarium.serving.manager import KohakuManager

manager = KohakuManager(session_dir="/var/kt/sessions")

agent_id = await manager.agent_create("@kt-biome/creatures/swe")
async for chunk in manager.agent_chat(agent_id, "Hi"):
    print(chunk, end="")

status = manager.agent_status(agent_id)
manager.agent_interrupt(agent_id)
await manager.agent_stop(agent_id)
```

也暴露 terrarium/creature/channel 的操作。Manager 会帮你处理 session store 挂载与并发存取安全。

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

简单情境下 `AgentSession` / `KohakuManager` 会根据 `session_dir` 自动处理。

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
- **热插拔的 Creature 收不到消息**。 调用完 `runtime.add_creature` 后，要对Creature该消费的每条频道调用 `runtime.wire_channel(..., direction="listen")`。
- **`AgentSession.chat` 卡住**。 另一个调用者正在使用这个代理；session 会串行化输入。每个调用者配一个 `AgentSession`。

## 延伸阅读

- [组合代数开发指南](composition.md) — 纯 Python 端的多代理管线。
- [自定义模块指南](custom-modules.md) — 自己写工具/输入/输出并接上来。
- [Reference / Python API 参考](../reference/python.md) — 完整签名。
- [examples/code/](../../examples/code/) — 各种 pattern 的可执行示例。
