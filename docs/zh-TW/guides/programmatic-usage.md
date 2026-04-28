---
title: 程式化使用
summary: 在你自己的 Python 程式碼裡驅動 Terrarium 引擎、Creature、Agent。
tags:
  - guides
  - python
  - embedding
---

# 程式化使用

給想要在自己的 Python 程式碼裡嵌入代理的讀者。

生物不是設定檔本身 — 設定檔只是它的描述。跑起來的生物是一個由 `Terrarium` 引擎托管的 async Python 物件。KohakuTerrarium 裡的所有東西都是可以 call、可以 await 的。你的程式碼才是那個 orchestrator；代理是你叫它跑的 worker。

觀念預備：[作為 Python 物件的代理](../concepts/python-native/agent-as-python-object.md)、[組合代數](../concepts/python-native/composition-algebra.md)。

## 入口

| 介面 | 什麼時候用 |
|---|---|
| `Terrarium` | 單一的執行期引擎。增加生物、連接它們、訂閱事件。同一個引擎處理獨立 agent 與多代理工作量。 |
| `Creature` | 引擎裡一隻運行中的生物 — `chat()`、`inject_input()`、`get_status()`。由 `Terrarium.add_creature` / `with_creature` 回傳。 |
| `Agent` | 較底層：生物背後的 LLM 控制器。要直接控制事件、觸發器或輸出 handler 時用。 |
| `AgentSession` | 舊版串流 wrapper。仍可用，但 `Creature.chat()` 是新的等價物。 |

頂層 import 是穩定的：`from kohakuterrarium import Terrarium, Creature, EngineEvent, EventFilter`。

要在 Python 裡做不靠引擎的多代理管線，看 [組合代數使用指南](composition.md)。

## `Terrarium` — 引擎

每個行程一個引擎，托管所有生物。獨立 agent 是 1-creature graph；recipe 是用頻道連起來的 connected graph。

### 獨立生物

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

`Terrarium.with_creature(config)` 建出引擎並把一隻生物放進 1-creature graph。回傳的 `Creature` 暴露 `chat()`、`inject_input()`、`is_running`、`graph_id`、`get_status()`。

### Recipe (多生物)

```python
import asyncio
from kohakuterrarium import Terrarium

async def main():
    engine = await Terrarium.from_recipe("@kt-biome/terrariums/swe_team")
    try:
        # 用 id 拿到引擎裡某隻生物
        swe = engine["swe"]
        async for chunk in swe.chat("Fix the off-by-one in pagination.py"):
            print(chunk, end="", flush=True)
    finally:
        await engine.shutdown()

asyncio.run(main())
```

Recipe 描述「加這些生物、宣告這些頻道、接這些 listen / send 邊」。`from_recipe()` 會走完它，把每隻生物都放進同一個 graph 並啟動它們。

### Async context manager

```python
async with Terrarium() as engine:
    alice = await engine.add_creature("@kt-biome/creatures/general")
    bob   = await engine.add_creature("@kt-biome/creatures/general")
    await engine.connect(alice, bob, channel="alice_to_bob")
    # ...
# 離開時自動 shutdown()
```

### 熱插拔

拓樸可以在執行期改變。跨 graph 的 `connect()` 會合併兩個 graph (environment 取聯集，掛著的 session store 合併成一份)。`disconnect()` 可能把 graph 拆開 (parent session 複製到兩邊)。

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")
    # a 和 b 各自處於獨立的 graph

    result = await engine.connect(a, b, channel="a_to_b")
    # result.delta_kind == "merge" — a 和 b 現在共用同一個 graph、
    # 同一個 environment、同一個 session store

    await engine.disconnect(a, b, channel="a_to_b")
    # 拆回兩個 graph；每邊都帶著合併後的歷史
```

參考 [`examples/code/terrarium_hotplug.py`](../../examples/code/terrarium_hotplug.py)。

### 訂閱引擎事件

```python
from kohakuterrarium import EventFilter, EventKind

async with Terrarium() as engine:
    async def watch():
        async for ev in engine.subscribe(
            EventFilter(kinds={EventKind.TOPOLOGY_CHANGED, EventKind.CREATURE_STARTED})
        ):
            print(ev.kind.value, ev.creature_id, ev.payload)
    asyncio.create_task(watch())
    # ... 驅動引擎
```

引擎裡所有可觀察的事 — 文字 chunk、頻道訊息、拓樸變更、session fork、錯誤 — 都以 `EngineEvent` 形式浮現。`EventFilter` 用 AND 把 kinds、creature ID、graph ID、頻道名組合起來。

### 關鍵方法

- `await Terrarium.with_creature(config)` — 引擎 + 一隻生物。
- `await Terrarium.from_recipe(recipe)` — 引擎 + 套用一份 recipe。
- `await Terrarium.resume(store)` — *尚未實作*。
- `await engine.add_creature(config, *, graph=None, start=True)` — 加進既有 graph 或開一個新的 singleton graph。
- `await engine.remove_creature(creature)` — 停掉並移除；可能拆 graph。
- `await engine.add_channel(graph, name, kind=...)` — 宣告頻道。
- `await engine.connect(a, b, channel=...)` — 接 `a → b`；需要時合併 graph。
- `await engine.disconnect(a, b, channel=...)` — 拆掉一條或全部邊；可能拆 graph。
- `await engine.wire_output(creature, sink)` / `await engine.unwire_output(creature, sink_id)` — 第二組輸出 sink。
- `engine[id]`、`id in engine`、`for c in engine`、`len(engine)` — Pythonic accessor。
- `engine.list_graphs()` / `engine.get_graph(graph_id)` — graph 檢視。
- `engine.status()` / `engine.status(creature)` — 整體或單隻生物的狀態 dict。
- `await engine.shutdown()` — 停掉每隻生物；冪等。

舊版 `TerrariumRuntime` 與 `KohakuManager` 在過渡期間還留在硬碟上、仍然能跑。新程式碼請用 `Terrarium`。

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

## 多租戶 server

HTTP API 把 `Terrarium` 引擎當成單行程的 singleton — 每個請求都透過引擎的 Pythonic accessor 以 ID 加、移、聊：

```python
from kohakuterrarium import Terrarium

engine = Terrarium(session_dir="/var/kt/sessions")

alice = await engine.add_creature("@kt-biome/creatures/swe", creature_id="alice")
async for chunk in alice.chat("Hi"):
    print(chunk, end="")

print(engine.status("alice"))
await engine.stop("alice")
```

FastAPI handler 這一邊，`kohakuterrarium.api.deps.get_engine()` 會回傳每個行程的 singleton；舊版的 `get_manager()` (`KohakuManager`) 在所有 route 都切過去之前還接著。

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

簡單情境下 `Terrarium(session_dir=...)` 會自動處理 — 把 `session_dir=` 傳給引擎，它就會在 `attach_session` 時掛上每個 graph 的 store。

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
- **熱插拔的生物收不到訊息。** 用 `engine.connect(sender, receiver, channel=...)` — 引擎會處理頻道註冊和觸發器注入。只 `add_creature` 會把生物放進一個沒有任何入站頻道的 singleton graph。
- **`AgentSession.chat` 卡住。** 另一個呼叫者正在使用這個代理；session 會串行化輸入。每個呼叫者配一個 `AgentSession`。

## 延伸閱讀

- [組合代數使用指南](composition.md) — 純 Python 端的多代理管線。
- [自訂模組](custom-modules.md) — 自己寫工具/輸入/輸出並接上來。
- [Reference / Python API](../reference/python.md) — 完整簽名。
- [examples/code/](../../examples/code/) — 各種 pattern 的可執行範例。
