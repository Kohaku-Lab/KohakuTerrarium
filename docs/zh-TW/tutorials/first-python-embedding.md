---
title: 在 Python 裡嵌入
summary: 在你自己的 Python 程式裡跑 agent：有型別的輪次、自訂工具、引擎托管的生物、session 檔與恢復。
tags:
  - tutorials
  - python
  - embedding
---

# 第一次 Python 嵌入

**問題：** 你想從自己的 Python 應用程式裡跑一隻生物 (creature)：
丟工作給它、觀察它在做什麼、留下紀錄、之後再恢復。

**完成狀態：** 一支最小的腳本：用 `Agent.build` 建 agent、用
`run` / `run_stream` 驅動有型別的輪次、用 `@kt.tool` 注入自訂工具、
把生物托管在帶 session 檔的 `Terrarium` 裡、用 `SessionReader`
把工作階段讀回來，最後恢復它。

**先備知識：** [第一隻生物](first-creature.md)。套件要裝成
可以 `import kohakuterrarium` 的模式。

在這個框架裡，agent 不是設定檔，而是一個 Python 物件。
設定檔只是描述它；`Agent.build(...)` 建構一個由你持有的實例。
心智模型見
[agent-as-python-object](../concepts/python-native/agent-as-python-object.md)。

## 步驟 1：Editable 安裝

目標：讓你的 venv import 得到 `kohakuterrarium`。

從 repo 根目錄：

```bash
uv pip install -e .[dev]
```

`[dev]` extras 會帶進之後可能用得到的測試輔助工具。

## 步驟 2：一個 agent、一個輪次

目標：建一個 agent、跑一個輪次、拿到有型別的結果。

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

跑起來：

```bash
python demo.py
```

注意三件事：

1. `Agent.build` 解析 `@kt-biome/...` 的方式跟 CLI 一樣，
   而且環境壞掉時會**拋錯** (`kt.errors.ConfigNotFoundError`、
   `LLMNotConfiguredError`…)，不會跑了半天什麼都沒產出。
2. `run()` 回傳 `TurnResult`：`status` (`"ok"` / `"error"` /
   `"timeout"` / `"interrupted"`)、`text`、`error`、`tool_calls`、
   `usage`、`duration_s`。失敗的輪次預設拋 `kt.errors.TurnError`；
   傳 `raise_on_error=False` 就自己依 `result.status` 分支。
3. `timeout=` 真的會中斷輪次：「逾時」之後不會有 token 繼續燒。

## 步驟 3：串流這個輪次

目標：文字一到就渲染，工具活動即時看到。

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

`run_stream` 產出一個有型別的 union（`TextChunk | Activity | TurnEnded`），
而且串流途中永遠不拋錯：錯誤會以
`Activity(kind="processing_error")` 出現，並寫進最終結果。

## 步驟 4：用普通函式給它一個工具

目標：用你自己的能力擴充 agent，不碰設定檔。

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

`@kt.tool` 從型別註記推導 schema、從 docstring 拿描述；
同步函式跑在執行緒裡，async 函式直接 await。也可以對活著的
agent 加能力（`agent.add_tool(...)`、`await agent.add_plugin(...)`），
系統提示詞會跟著更新，控制器真的看得到。

## 步驟 5：托管進引擎，帶一個 session 檔

目標：每隻生物自己的工作目錄 + 一個可恢復的 session 檔，
完全沒有持久化儀式。

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    async with Terrarium() as engine:
        clerk = await engine.add_creature(
            "@kt-biome/creatures/general",
            pwd="workdir",                        # 生物的 cwd
            session="runs/clerk.kohakutr",        # 自動建立 + 自動關閉
        )
        result = await clerk.run("Summarize the files in this directory.")
        print(result.text)


asyncio.run(main())
```

引擎可以托管任意數量的生物
([`examples/code/batch_grading.py`](../../../examples/code/batch_grading.py)
裡的[批次模式](../guides/programmatic-usage.md#標準批次模式)
用 semaphore 控制、每個繳交資料夾一隻生物)。離開
`async with` 區塊會停止所有生物、關閉引擎建立的每個 session
store。改用 `Terrarium(session_dir="runs/")` 則是每張圖自動持久化。

## 步驟 6：把工作階段讀回來

目標：離線檢視發生了什麼，不動到檔案的狀態。

```python
from kohakuterrarium import SessionReader

with SessionReader("runs/clerk.kohakutr") as r:
    print(r.meta["session_id"], r.meta["status"])
    for turn in r.turns():
        tools = [tc["name"] for tc in turn.tool_calls]
        print(f"- {turn.user_text[:40]!r} -> {turn.assistant_text[:60]!r} {tools}")
```

`SessionReader` 是唯讀的 (透過 `SessionStore.open_readonly` 開檔)，
檢視永遠不會更新 `last_active` 或改動 `status`。

## 步驟 7：恢復它

目標：在新的行程裡把對話接回來。

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

`Terrarium.resume` 從 session metadata 記錄的 config path 重建拓樸，
並回灌已儲存的對話。`engine.adopt_session(...)` 做同樣的事，
但對象是一個已經在跑其他圖的引擎。

## 你學到了什麼

- `Agent.build` 是標準建構子；它拋出型別化的
  `kt.errors.*` 例外，而不是默默降級。
- `run()` 回傳 `TurnResult`；`run_stream()` 產出有型別的事件；
  `timeout=` 是真的會中斷。
- `@kt.tool` 把普通函式變成 agent 工具；用 `tools=` /
  `add_tool` 注入。
- `Terrarium` 托管生物，每隻有自己的 `pwd` 和 `session=`
  持久化；`SessionReader` 把檔案讀回來；`Terrarium.resume`
  把它接下去。

## 接下來讀什麼

- [程式化使用指南](../guides/programmatic-usage.md)：Python 介面
  的任務導向參考，包括引擎事件、熱插拔與驗證。
- [組合代數](../guides/composition.md)：`>>`、`&`、`|`、
  `*` 運算子，用於請求範圍的 pipeline。
- [工作階段使用指南](../guides/sessions.md)：`.kohakutr` 檔案的一切。
- [Python API 參考](../reference/python.md)：精確簽名。
