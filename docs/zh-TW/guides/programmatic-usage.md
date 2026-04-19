---
title: 程式化使用
summary: 在你自己的 Python 程式碼裡驅動 Agent、AgentSession、TerrariumRuntime、KohakuManager。
tags:
  - guides
  - python
  - embedding
---

# 程式化使用

給想要在自己的 Python 程式碼裡嵌入代理的讀者。

生物不是設定檔本身 — 設定檔只是它的描述。跑起來的生物是一個 async Python 物件：`Agent`。KohakuTerrarium 裡的所有東西 (包含生態瓶與 session) 都是可以 call、可以 await 的。你的程式碼才是那個 orchestrator；代理是你叫它跑的 worker。

觀念預備：[作為 Python 物件的代理](../concepts/python-native/agent-as-python-object.md)、[組合代數](../concepts/python-native/composition-algebra.md)。

## 四個入口

| 介面 | 什麼時候用 |
|---|---|
| `Agent` | 你想要完整控制權：注入事件、接自訂輸出、自己管理 lifecycle。 |
| `AgentSession` | 串流式聊天的 wrapper：注入輸入、逐 chunk 走過輸出。拿來做 bot 或 web UI 都合適。 |
| `TerrariumRuntime` | 有一份生態瓶 config，想把它跑起來。 |
| `KohakuManager` | 多租戶 server：多個代理/生態瓶以 ID 管理，與傳輸層無關。 |

要在 Python 裡做多代理管線而不建生態瓶，看 [組合代數使用指南](composition.md)。

## `Agent` — 完整控制權

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

關鍵方法：

- `Agent.from_path(path, *, input_module=..., output_module=..., session=..., environment=..., llm_override=..., pwd=...)` — 從 config 資料夾或 `@pkg/...` 參照建出代理。
- `await agent.start()` / `await agent.stop()` — lifecycle。
- `await agent.run()` — 內建主迴圈 (從輸入拉事件、派發觸發器、跑控制器)。
- `await agent.inject_input(content, source="programmatic")` — 繞過輸入模組直接推輸入。
- `await agent.inject_event(TriggerEvent(...))` — 推任何事件。
- `agent.interrupt()` — 中止當前處理週期 (非阻塞)。
- `agent.switch_model(profile_name)` — 執行期換 LLM。
- `agent.set_output_handler(fn, replace_default=False)` — 新增或取代輸出 sink。
- `await agent.add_trigger(trigger)` / `await agent.remove_trigger(id)` — 執行期管觸發器。

屬性：

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

`chat(message)` 會在控制器串流時 yield 文字 chunk。工具活動與子代理事件透過輸出模組的 activity callback 表面化 — `AgentSession` 專注在文字流；要更豐富的事件請用 `Agent` 配自訂輸出模組。

Builder：`AgentSession.from_path(...)`、`from_config(AgentConfig)`、`from_agent(pre_built_agent)`。

## 接輸出

`set_output_handler` 讓你掛任何 callable：

```python
def handle(text: str) -> None:
    my_logger.info(text)

agent.set_output_handler(handle, replace_default=True)
```

多個 sink (TTS、Discord、檔案) 的話，在 YAML 設定 `named_outputs`，代理會自動路由。

## 事件層控制

```python
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event

await agent.inject_event(create_user_input_event("Hi", source="slack"))
await agent.inject_event(TriggerEvent(
    type="context_update",
    content="User just navigated to page /settings.",
    context={"source": "frontend"},
))
```

`type` 可以是任何控制器接得住的字串 — `user_input`、`idle`、`timer`、`channel_message`、`context_update`、`monitor`，或你自己定義的。見 [reference/python](../reference/python.md)。

## 從 code 跑生態瓶

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

Runtime 方法：`start`、`stop`、`run`、`add_creature`、`remove_creature`、`add_channel`、`wire_channel`。`environment` 裡有 `shared_channels` (一個 `ChannelRegistry`)，所有生物都看得到；每隻生物有自己私有的 `Session`。

## `KohakuManager` — 多租戶

HTTP API、web app、以及任何需要「用 ID 管多個代理」的程式碼都用它：

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

也暴露 terrarium/creature/channel 的操作。Manager 會幫你處理 session store 掛載與併發存取安全。

## 乾淨地停下來

永遠把 `start()` 跟 `stop()` 配對：

```python
agent = Agent.from_path("...")
try:
    await agent.start()
    await agent.inject_input("...")
finally:
    await agent.stop()
```

或用 `AgentSession` / `compose.agent()`，它們是 async context manager。

Interrupt 在任何 asyncio task 裡都安全：

```python
agent.interrupt()           # 非阻塞
```

控制器在 LLM 串流步驟之間會檢查 interrupt 旗標。

## 自訂 session / environment

```python
from kohakuterrarium.core.session import Session
from kohakuterrarium.core.environment import Environment

env = Environment(env_id="my-app")
session = env.get_session("my-agent")
session.extra["db"] = my_db_connection

agent = Agent.from_path("...", session=session, environment=env)
```

放進 `session.extra` 的東西，工具可以透過 `ToolContext.session` 讀到。

## 掛 session 持久化

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

簡單情境下 `AgentSession` / `KohakuManager` 會根據 `session_dir` 自動處理。

## 測試

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

`ScriptedLLM` 是決定性的；`OutputRecorder` 會抓 chunk/write/activity 供 assert。

## 疑難排解

- **`await agent.run()` 永遠不回來。** `run()` 是完整的事件迴圈；輸入模組關掉 (例如 CLI 收到 EOF) 或終止條件觸發時才會結束。要做 one-shot 互動請改用 `inject_input` + `stop`。
- **輸出 handler 沒被呼叫。** 如果你不想連 stdout 一起出，記得設 `replace_default=True`；並確認代理在 inject 之前已經 start。
- **熱插拔的生物收不到訊息。** 呼叫完 `runtime.add_creature` 後，要對生物該消費的每條頻道呼叫 `runtime.wire_channel(..., direction="listen")`。
- **`AgentSession.chat` 卡住。** 另一個呼叫者正在使用這個代理；session 會串行化輸入。每個呼叫者配一個 `AgentSession`。

## 延伸閱讀

- [組合代數使用指南](composition.md) — 純 Python 端的多代理管線。
- [自訂模組](custom-modules.md) — 自己寫工具/輸入/輸出並接上來。
- [Reference / Python API](../reference/python.md) — 完整簽名。
- [examples/code/](../../examples/code/) — 各種 pattern 的可執行範例。
