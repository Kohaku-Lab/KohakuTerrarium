---
title: 程式化使用
summary: 用你自己的 Python 程式驅動 Agent、Terrarium 與 Creature：有型別的輪次、嚴格的錯誤、引擎持有的工作階段。
tags:
  - guides
  - python
  - embedding
---

# 程式化使用

寫給想把 agent 嵌進自己 Python 程式的讀者。

生物 (creature) 不是一個設定檔，設定檔只是描述它。運行中的
agent 是一個 async Python 物件，而整個程式化介面建立在三個承諾上：

1. **有型別的輪次。** `run()` 回傳 `TurnResult` (status、text、
   tool calls、usage、duration)；`run_stream()` 即時產出有型別的事件。
2. **嚴格的錯誤。** 程式化建構子與輪次會**拋出**型別化的
   `kt.errors.*` 例外，而不是默默降級：
   掛掉的 provider 是一個例外，不是一個乾淨的空回覆。
3. **引擎持有的工作階段。** 持久化是一個關鍵字參數
   (`session=`、`Terrarium(session_dir=...)`)，不是一套儀式。

精確簽名見 [reference/python](../reference/python.md)。

## 入口

| 介面 | 什麼時候用 |
|---|---|
| `Agent` | 一個 agent、不需要引擎功能。`await Agent.build(...)` 之後 `run` / `run_stream`。 |
| `Terrarium` | 執行期引擎。每隻生物獨立的工作目錄、session 檔、頻道、熱插拔、事件。只要跑超過一個 agent（或一個你想持久化的 agent）就用它。 |
| `Creature` | 引擎內運行中的 agent：`run`、`run_stream`、`attach`、`get_status`。由 `add_creature` / `with_creature` 回傳。 |
| `Studio` | 引擎之上的管理門面 (目錄、已儲存的工作階段、編輯器)。見 [Studio 使用指南](studio.md)。 |
| `compose` | 請求範圍的 pipeline (`>>`、`&`、`\|`、`*`)，見[組合](composition.md)。 |

頂層 import：`from kohakuterrarium import Agent, Terrarium,
Creature, TurnResult, TextChunk, Activity, TurnEnded, SessionReader,
tool, errors, validate`。

## 一個 agent、一個輪次

```python
import asyncio
from kohakuterrarium import Agent, TextChunk, TurnEnded

async def main():
    agent = await Agent.build("@kt-biome/creatures/general")
    await agent.start()
    try:
        # 緩衝式：一個 TurnResult，帶 status / text / usage。
        result = await agent.run("What is a terrarium?", timeout=300)
        print(result.text)
        if result.usage:
            print(f"[{result.usage.get('total_tokens', '?')} tokens]")

        # 串流式：事件發生時即時拿到。
        async for event in agent.run_stream("How would you build one?"):
            if isinstance(event, TextChunk):
                print(event.text, end="", flush=True)
            elif isinstance(event, TurnEnded):
                print(f"\n[turn status: {event.result.status}]")
    finally:
        await agent.stop()

asyncio.run(main())
```

(完整腳本：[`examples/code/programmatic_chat.py`](../../../examples/code/programmatic_chat.py)。)

`Agent.build` 接受設定資料夾路徑、`@pkg/...` 套件參照，或一個
已載入的 `AgentConfig`。它回傳的 agent **尚未啟動**：
`await agent.start()` 一定要跟 `await agent.stop()` 成對
(`Agent` 沒有 `async with`)。

`agent.run_forever()` 是舊式的自主主迴圈 (由輸入模組 + 觸發器
驅動 agent，直到輸入結束)，`kt run` 走的就是它。
腳本幾乎都該改用 `run` / `run_stream`。

## 什麼會拋錯、什麼時候拋

程式化介面預設嚴格：

- **建構** (`Agent.build`、`engine.add_creature`)：設定不存在或
  套件未安裝拋 `kt.errors.ConfigNotFoundError`，模型解析不出來拋
  `LLMNotConfiguredError`，不認識的工具 / 壞掉的外掛也會拋錯。
  互動式前端會傳 `strict=False` 改成降級處理。
- **輪次**：失敗拋 `TurnError`、逾時拋 `TurnTimeoutError`。
  `timeout=` 真的會**中斷**輪次 (不會放任一個還在燒的 LLM 呼叫
  不管)。傳 `raise_on_error=False` 可以永遠拿回 `TurnResult`，
  自己依 `result.status`
  (`"ok"` / `"error"` / `"timeout"` / `"interrupted"`) 分支，
  批次任務就適合這個形狀。
- `run_stream` 迭代中永遠不拋錯：錯誤以
  `Activity(kind="processing_error")` 事件出現，並寫進最後的
  `TurnEnded(result)`。

```python
from kohakuterrarium import errors

try:
    result = await agent.run("Grade this submission.", timeout=1800)
except errors.TurnTimeoutError:
    print("over budget; turn was interrupted")
except errors.TurnError as e:
    print(f"turn failed: {e}")
```

跑長任務之前先用
[`kt.validate`](../reference/python.md#validate) 驗證環境：
`validate.config`、`validate.llm`、`validate.creature` (完整 dry-run
建構)、`await validate.ping` (一次真實來回)。CLI 的對應是 `kt doctor`。

## 帶上你自己的工具、外掛與 LLM

`@kt.tool` 把普通函式變成 agent 工具：schema 從型別註記來，
描述從 docstring 來。同步函式跑在執行緒裡；async 函式直接 await。

```python
import kohakuterrarium as kt

@kt.tool
def check_stock(item: str) -> str:
    """Look up how many units of an item are in stock."""
    return lookup(item)

agent = await kt.Agent.build(
    "@kt-biome/creatures/general",
    llm="default",                 # profile 名稱，打錯字會在這裡就拋錯
    tools=[check_stock],           # 實例，進到初始提示詞
    plugins=[MyTracePlugin()],
)
```

建構之後也可以擴充一個活著的 agent，每次呼叫都會更新
系統提示詞，控制器真的看得到變化：

```python
agent.add_tool(other_tool)
await agent.add_plugin(plugin)     # 啟動後加入也會觸發 on_load
agent.add_subagent(subagent_cfg)
```

`llm=` 在所有地方都接受四種形狀 (`Agent.build`、
`engine.add_creature`、`compose.agent`)：

- `None`：從設定解析；
- 選擇器字串：profile / preset 名稱或
  `provider/model[@variations]`；
- 一個 `LLMProfile` 實例；
- 一個 provider 實例，例如測試用的 `ScriptedLLM`。

`io=` 決定設定裡的 I/O 啟動多少：`"config"` (依宣告)、
`"none"` (停用輸入)、`"headless"` (停用輸入且靜音預設輸出；
批次任務的預設，N 個並行 agent 才不會在你的 console 上交錯輸出)。

## 引擎：`Terrarium`

每個行程一個引擎，托管所有生物；獨立 agent 就是一張單生物圖。
需要每隻生物獨立的工作目錄、session 檔、頻道或執行期拓樸時，
就拿出引擎。

### 標準批次模式

一個共用引擎、每個工作資料夾一隻生物，各自有自己的
`pwd` 和 session 檔
([`examples/code/batch_grading.py`](../../../examples/code/batch_grading.py))：

```python
import asyncio
from kohakuterrarium import Terrarium

async def grade_one(engine, folder, gate):
    async with gate:
        creature = await engine.add_creature(
            "@kt-biome/creatures/general",
            llm="default",
            pwd=folder,                                   # 不動全域 os.chdir
            session=folder / "scoring_session.kohakutr",  # 之後可恢復
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

配方描述的是「加入這些生物、宣告這些頻道、接好這些 listen/send
邊」。`from_recipe` 會把所有生物放進同一張圖並啟動。給
`apply_recipe` 加 `session=` (或用 `session_dir=` 建引擎)
就能持久化整張圖。

### 熱插拔與拓樸

拓樸可以在執行期變動。跨圖的 `connect()` 會自動合併兩張圖
(環境取聯集、session store 合併)；`disconnect()` /
`remove_creature()` 可能觸發自動分割。圖層的所有頻道都是廣播：
每個監聽者都收到每一則訊息。

```python
async with Terrarium() as engine:
    a = await engine.add_creature("@kt-biome/creatures/general")
    b = await engine.add_creature("@kt-biome/creatures/general")

    result = await engine.connect(a, b, channel="a_to_b")
    # result.delta_kind == "merge"：一張圖、一個環境

    d = await engine.disconnect(a, b, channel="a_to_b")
    # d.delta_kind == "split"：又變回兩張圖，歷史各複製一份
```

(完整腳本：[`examples/code/terrarium_hotplug.py`](../../../examples/code/terrarium_hotplug.py)。)

引擎為圖的即時狀態提供公開存取器，不用去戳私有 dict：

```python
from kohakuterrarium.core.channel import ChannelMessage

graph_id = engine.list_graphs()[0].graph_id
env = engine.environment(graph_id)          # 即時的 Environment
tasks = engine.channel(graph_id, "tasks")   # 即時的廣播頻道，或 None
if tasks is not None:
    await tasks.send(ChannelMessage(sender="user", content="Fix the bug"))
```

### 觀察引擎事件

引擎匯流排承載**結構**事件 (生物加入 / 啟動 / 停止、拓樸變化、
頻道訊息、接線)；各生物的文字與工具活動走輪次介面
(`run_stream` / `attach`)。

```python
from kohakuterrarium import EventFilter, EventKind

async def watch(engine):
    async for ev in engine.subscribe(
        EventFilter(kinds={EventKind.TOPOLOGY_CHANGED, EventKind.CREATURE_STARTED})
    ):
        print(ev.kind.value, ev.creature_id, ev.payload)
```

訂閱者在 `subscribe()` 呼叫當下就完成註冊，第一次 `await` 之前
發出的事件會被緩衝：「先訂閱、再觸發」這個寫法不會弄丟
第一個事件。`engine.shutdown()` 會終結所有活著的訂閱者。

## `Creature`：運行中的 handle

`Creature` 鏡射 agent 的輪次介面，再加上引擎側的脈絡：

- `await creature.run(content, timeout=..., raise_on_error=...)` → `TurnResult`
- `creature.run_stream(content)` → 有型別的事件
- `creature.attach()`：**非破壞性觀察者**：一個 async context
  manager，串流這隻生物發出的每個型別化事件，包括帶外的輪次
  (觸發器、頻道訊息)。可多消費者；預設輸出與 session store
  仍照常收到一切。
- `await creature.chat(message)`：純文字語法糖；新程式碼請用
  有型別的驅動方法。
- `creature.status`：`"not_started"` / `"idle"` / `"busy"` /
  `"stopped"` / `"error"`；`creature.get_status()` 拿完整 dict。

```python
async with creature.attach() as stream:
    async for ev in stream:
        log(ev)          # 工具啟動、文字、錯誤，全部都有
```

## 從程式碼操作工作階段

持久化由引擎持有 (舊的 `SessionStore` + `init_meta` +
`attach_session` 儀式已經不在了)：

```python
# Autosession：每張圖自動拿到 runs/<graph_id>.kohakutr。
engine = Terrarium(session_dir="runs/")

# 或逐生物指定：精確檔案、True (預設目錄)、False (關閉)，或一個 store。
c = await engine.add_creature("@kt-biome/creatures/general",
                              session="runs/student-42.kohakutr")

# 之後恢復：開新引擎，或恢復進運行中的引擎。
engine2 = await Terrarium.resume("runs/student-42.kohakutr")
graph_id = await engine.adopt_session("runs/other.kohakutr")
```

`engine.shutdown()` 會關閉它建立的每個 store。已完成的檔案用
`SessionReader` 讀 (meta、事件、重組的輪次、搜尋)，見
[工作階段](sessions.md)。

## 測試你的整合

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

`engine.add_creature(path, llm=ScriptedLLM([...]))` 用法相同。

## 乾淨地收尾

- `Agent`：用 `try/finally` 讓 `start()` / `stop()` 成對。
- `Terrarium`：用 `async with`：離開時跑 `shutdown()`，停止所有
  生物，並關閉引擎建立的每個 session store。
- `agent.interrupt()` / `creature.agent.interrupt()` 可以從任何
  asyncio task 取消進行中的輪次 (非阻塞)。

## 疑難排解

- **`await agent.run_forever()` 一直不返回。** 那是自主主迴圈；
  要等輸入模組關閉或終止條件觸發才會退出。一次性互動請用
  `run` / `run_stream`。
- **第一次呼叫就 `TurnError: turn failed`。** Provider 呼叫失敗了：
  先跑 `kt.validate.llm("<selector>")` 和
  `await kt.validate.ping(...)`，再懷疑自己的程式碼。
- **熱插拔進來的生物永遠收不到訊息。** 要用
  `engine.connect(sender, receiver, channel=...)`；光是
  `add_creature` 只會給它一張沒有任何入站頻道的單生物圖。
- **同一個 agent 同時跑兩個 `run()`。** 輪次在 agent 的
  processing lock 上序列化；第二個 `run` 會等第一個。要平行就用
  多隻生物 (批次模式)。
- **N 個並行 agent 把 console 弄得很吵。** 傳 `io="headless"`
  靜音設定裡的預設 stdout 輸出；文字改從
  `run` / `run_stream` / session store 拿。

## 另見

- [組合](composition.md)：請求範圍的 pipeline。
- [工作階段](sessions.md)：持久化、恢復、`SessionReader`。
- [套件](packages.md)：`@pkg/...` 參照與 `packages.ensure`。
- [參考 / Python API](../reference/python.md)：精確簽名。
- [`examples/code/`](../../../examples/code/)：每種模式的可執行
  腳本 (`batch_grading.py` 是批次任務的標準範例)。
