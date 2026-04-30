---
title: 程式化使用
summary: 在你自己的 Python 程式碼裡驅動 Studio、Terrarium、Creature 與底層 Agent。
tags:
  - guides
  - python
  - embedding
---

# 程式化使用

給想要在自己的 Python 程式碼裡嵌入代理的讀者。

Creature 不是設定檔本身——設定檔只是它的描述。運行起來的 Creature 是一個由 `Terrarium` 引擎托管的 async Python 物件。`Studio` 是引擎之上的管理 facade：catalog、identity、active sessions、saved sessions、attach policies 與 editor workflows。KohakuTerrarium 裡的所有東西都是可以 call、可以 await 的。你的程式碼才是 orchestrator；代理是你叫它跑的 worker。

相關概念：[Studio](../concepts/studio.md)、[Terrarium](../concepts/multi-agent/terrarium.md)、[作為 Python 物件的代理](../concepts/python-native/agent-as-python-object.md)、[組合代數](../concepts/python-native/composition-algebra.md)。

## 入口

| 介面 | 什麼時候用 |
|---|---|
| `Studio` | 管理 facade。用於 packages/catalog、settings/identity、active sessions、saved sessions、attach policy 和 editor workflows。 |
| `Terrarium` | 執行期引擎。增加 Creature、連接它們、訂閱事件。同一個引擎處理獨立與多 Creature 工作負載。 |
| `Creature` | 引擎裡一隻運行中的 Creature：`chat()`、`inject_input()`、`get_status()`。由 `Terrarium.add_creature` / `with_creature` 回傳。 |
| `Agent` | 較底層：Creature 背後的 LLM 控制器。需要直接控制事件、觸發器或輸出 handler 時使用。 |

頂層 import 是穩定的：`from kohakuterrarium import Studio, Terrarium, Creature, EngineEvent, EventFilter`。

要在 Python 裡做輕量 request pipeline、但不想手動管理長生命週期 terrarium graph 或 recipe，請看 [組合](composition.md)。

## `Studio` — 管理 facade

當你嵌入的是 CLI / dashboard 的職責時用 `Studio`：啟動、列出和停止 sessions，恢復 `.kohakutr`，查看 packages，編輯 workspace，或暴露 attach streams。

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

常用建構 helper：

- `Studio()` — 建立帶空引擎的 Studio。
- `await Studio.with_creature(config, *, pwd=None)` — 建立 Studio + 單 Creature session。
- `await Studio.from_recipe(recipe, *, pwd=None)` — 建立 Studio + recipe session。
- `await Studio.resume(store, *, pwd=None, llm_override=None)` — 把保存的 session 恢復進新 Studio。
- `await studio.shutdown()` — 停掉底層引擎；`async with` 會自動呼叫。

重要 namespace：

- `studio.sessions` — active graph/session lifecycle 與 per-creature chat/control/state helpers。
- `studio.catalog` — packages、built-ins、workspace creature/module listing、introspection。
- `studio.identity` — LLM profiles/backends、API keys、Codex auth、MCP、UI preferences。
- `studio.persistence` — saved-session list/resolve/resume/fork/history/viewer/export/delete。
- `studio.attach` — chat、channel observer、trace/logs、files、pty 的 live attach policy。
- `studio.editors` — workspace creature/module scaffolding 與寫入。

更多 task-oriented 範例見 [Studio 指南](studio.md)。

## `Terrarium` — 引擎

每個行程一個引擎，托管所有 Creature。獨立代理是 1-creature graph；recipe 是帶 channel 的 connected graph。

### 獨立 Creature

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

`Terrarium.with_creature(config)` 建出引擎並把一隻 Creature 放進 1-creature graph。回傳的 `Creature` 暴露 `chat()`、`inject_input()`、`is_running`、`graph_id`、`get_status()`。

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

Recipe 描述「加入這些 Creature、宣告這些 channel、接這些 listen/send edges」。`from_recipe()` 會走完它，把每隻 Creature 都放進同一個 graph 並啟動它們。

### Async context manager

```python
async with Terrarium() as engine:
    alice = await engine.add_creature("@kt-biome/creatures/general")
    bob = await engine.add_creature("@kt-biome/creatures/general")
    await engine.connect(alice, bob, channel="alice_to_bob")
    # ...
# 離開時自動 shutdown()
```

### 熱插拔

拓撲可以在執行時改變。跨 graph 的 `connect()` 會合併兩個 graph（environment 取聯集，掛著的 session store 合併成一份）。`disconnect()` 可能把 graph 拆開（parent session 複製到兩邊）。

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")

    result = await engine.connect(a, b, channel="a_to_b")
    # result.delta_kind == "merge"

    await engine.disconnect(a, b, channel="a_to_b")
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
```

引擎裡所有可觀察的事——文字 chunk、channel message、拓撲變更、session fork、錯誤——都以 `EngineEvent` 形式浮現。`EventFilter` 用 AND 把 kinds、creature ID、graph ID、channel 名組合起來。

### 關鍵方法

- `await Terrarium.with_creature(config)` — 引擎 + 一隻 Creature。
- `await Terrarium.from_recipe(recipe)` — 引擎 + 套用一份 recipe。
- `await Terrarium.resume(store, *, pwd=None, llm_override=None)` — 建引擎並採用 saved session。
- `await engine.adopt_session(store, *, pwd=None, llm_override=None)` — 恢復進既有引擎並回傳 graph id。
- `await engine.add_creature(config, *, graph=None, start=True)` — 加進既有 graph 或開一個新的 singleton graph。
- `await engine.remove_creature(creature)` — 停掉並移除；可能拆 graph。
- `await engine.add_channel(graph, name, kind=...)` — 宣告 channel。
- `await engine.connect(a, b, channel=...)` — 接 `a → b`；需要時合併 graph。
- `await engine.disconnect(a, b, channel=...)` — 拆掉一條或全部邊；可能拆 graph。
- `await engine.wire_output(creature, sink)` / `await engine.unwire_output(creature, sink_id)` — 第二組輸出 sink。
- `engine[id]`、`id in engine`、`for c in engine`、`len(engine)` — Pythonic accessor。
- `engine.list_graphs()` / `engine.get_graph(graph_id)` — graph 檢視。
- `engine.status()` / `engine.status(creature)` — 整體或單隻 Creature 的狀態 dict。
- `await engine.shutdown()` — 停掉每隻 Creature；冪等。

執行期機制用 `Terrarium`；如果還需要 catalog、settings、saved-session、attach 或 editor 管理，用 `Studio`。

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

- `Agent.from_path(path, *, input_module=..., output_module=..., session=..., environment=..., llm_override=..., pwd=...)` — 從 config 目錄或 `@pkg/...` 參照建出代理。
- `await agent.start()` / `await agent.stop()` — lifecycle。
- `await agent.run()` — 內建主迴圈（從輸入拉事件、派發觸發器、跑控制器）。
- `await agent.inject_input(content, source="programmatic")` — 繞過輸入模組直接推輸入。
- `await agent.inject_event(TriggerEvent(...))` — 推任何事件。
- `agent.interrupt()` — 中止目前處理週期（非阻塞）。
- `agent.switch_model(profile_name)` — 執行期換 LLM。
- `agent.llm_identifier()` — 讀取規範化的 `provider/name[@variations]` 標識。
- `agent.set_output_handler(fn, replace_default=False)` — 新增或取代輸出 sink。
- `await agent.add_trigger(trigger)` / `await agent.remove_trigger(id)` — 執行期管觸發器。

屬性：

- `agent.is_running: bool`
- `agent.tools: list[str]`、`agent.subagents: list[str]`
- `agent.conversation_history: list[dict]`

## `Creature` — 串流聊天

`Creature.chat(message)` 會在控制器串流時 yield 文字 chunk。工具活動與子代理事件仍然透過底層 output/event path 表面化；`Creature` 專注在簡單文字流與狀態句柄。

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

當你只想推輸入、不消費輸出時用 `Creature.inject_input(message, source=...)`；需要 model、tools、sub-agents、graph id、channels、working directory 狀態時用 `Creature.get_status()`。

## 接輸出

`set_output_handler` 讓你掛任何 callable：

```python
def handle(text: str) -> None:
    my_logger.info(text)

agent.set_output_handler(handle, replace_default=True)
```

多個 sink（TTS、Discord、檔案）的話，在 YAML 設定 `named_outputs`，代理會自動路由。

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

`type` 可以是任何控制器接得住的字串——`user_input`、`idle`、`timer`、`channel_message`、`context_update`、`monitor`，或你自己定義的。見 [reference/python 參考](../reference/python.md)。

## 多租戶 server

HTTP API 把 `Studio` 當成共享 `Terrarium` 引擎之上的管理 facade。API route 透過 Studio namespace 啟動 sessions、與 creatures 聊天、查看 settings、恢復 saved sessions，而不是重複實作這些政策。你自己的 server 也建議用同樣形狀：

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

FastAPI handler 會透過 dependency helper 取得每個行程的 `Studio` / `Terrarium` 物件。Route handler 應把 catalog、identity、active-session、persistence、attach 與 editor policy 交給 Studio namespace。

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

或者在適合時使用 `Terrarium`、`Studio` 或 `compose.agent()` 作為 async context manager。

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

簡單情境下 `Terrarium(session_dir=...)` 會自動處理——把 `session_dir=` 傳給引擎，它就會在 `attach_session` 時掛上每個 graph 的 store。

如果 agent 會產生 binary artifacts（例如 provider-native images），請在執行前 attach session store，這樣 artifacts 才會與 session file 一起持久化到 `<session>.artifacts/`。

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

- **`await agent.run()` 一直不返回。** `run()` 是完整事件迴圈；輸入模組關掉（例如 CLI 收到 EOF）或終止條件觸發時才會結束。要做 one-shot 互動請改用 `inject_input` + `stop`。
- **輸出 handler 沒有被呼叫。** 如果你不想連 stdout 一起出，記得將 `replace_default=True`；並確認代理在 inject 之前已經 start。
- **熱插拔的 Creature 收不到訊息。** 用 `engine.connect(sender, receiver, channel=...)`——引擎會處理 channel 註冊和 trigger 注入。只 `add_creature` 會把 Creature 放進一個沒有任何入站 channel 的 singleton graph。
- **`Creature.chat` 看起來卡住。** 另一個呼叫者可能正在使用同一隻 Creature；請按 Creature 串行化訪問，或為獨立呼叫者啟動分開的 session / Creature。

## 延伸閱讀

- [組合](composition.md) — Python 端的多代理管線。
- [自訂模組指南](custom-modules.md) — 自己寫工具/輸入/輸出並接上來。
- [Reference / Python API 參考](../reference/python.md) — 完整簽名。
- [examples/code/](../../examples/code/) — 各種 pattern 的可執行範例。
