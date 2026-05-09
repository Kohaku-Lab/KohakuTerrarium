---
title: 第一個 Python 嵌入範例
summary: 用 Creature.chat、Studio 與組合代數，在你自己的 Python 程式碼中執行代理。
tags:
  - tutorials
  - python
  - embedding
---

# 第一個 Python 嵌入範例

## 你將建構什麼

**完成狀態：**一支最小腳本：透過 `Terrarium` 啟動 Creature，用 `Creature.chat()` 串流輸出，嵌入一個多 Creature Terrarium，並用 Studio 管理 session。最後附上底層 `Agent` 範例，用於自訂 output handler。

## 步驟 1 —— 安裝開發環境

從倉庫根目錄：

```bash
uv pip install -e .[dev]
```

`[dev]` extras 會帶入稍後可能會用到的測試輔助工具。

## 步驟 2 —— 用 `Creature.chat()` 做最小嵌入

目標：建構一隻運行中的 Creature、送出一條輸入、串流它的回應，並乾淨地關閉引擎。

`demo.py`：

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    engine, creature = await Terrarium.with_creature(
        "@kt-biome/creatures/general"
    )

    try:
        async for chunk in creature.chat(
            "In one sentence, what is a creature in KohakuTerrarium?"
        ):
            print(chunk, end="", flush=True)
        print()
    finally:
        await engine.shutdown()


asyncio.run(main())
```

執行：

```bash
python demo.py
```

注意三件事：

1. `Terrarium.with_creature` 解析 `@kt-biome/...` 的方式與 CLI 相同。
2. 單隻 Creature 也由多 Creature graph 使用的同一個引擎托管。
3. `Creature.chat(...)` 是一個文字 chunk 的 async iterator。

## 步驟 3 —— 只推輸入，不消費輸出

目標：從你自己的 scheduler、bot 或 event loop 餵輸入。若另一個 output sink 負責渲染，就使用 `inject_input(...)`。

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    engine, creature = await Terrarium.with_creature(
        "@kt-biome/creatures/general"
    )
    try:
        await creature.inject_input(
            "Explain the difference between a creature and a terrarium."
        )
    finally:
        await engine.shutdown()


asyncio.run(main())
```

Creature 會使用它設定的 output module。若只是想在自己的程式碼裡拿簡單文字流，優先用 `Creature.chat(...)`。

## 步驟 4 —— 用底層 `Agent` 擷取輸出

目標：把輸出導進你自己的 handler，而不是 stdout。這是自訂 transport 的進階形狀；大多數應用應從 `Creature.chat()` 或 `Studio.sessions.chat` 開始。

```python
import asyncio

from kohakuterrarium.core.agent import Agent


async def main() -> None:
    parts: list[str] = []

    agent = Agent.from_path("@kt-biome/creatures/general")
    agent.set_output_handler(
        lambda text: parts.append(text),
        replace_default=True,
    )

    await agent.start()
    try:
        await agent.inject_input(
            "Describe three practical uses of a terrarium."
        )
    finally:
        await agent.stop()

    print("".join(parts))


asyncio.run(main())
```

`replace_default=True` 會關閉 stdout，讓你的 handler 成為唯一 sink。

## 步驟 5 —— 嵌入整個 Terrarium

目標：從 Python 驅動多代理 setup，而不是透過 CLI。

```python
import asyncio

from kohakuterrarium import Terrarium


async def main() -> None:
    async with await Terrarium.from_recipe(
        "@kt-biome/terrariums/swe_team"
    ) as engine:
        swe = engine["swe"]
        async for chunk in swe.chat("Fix the auth bug."):
            print(chunk, end="", flush=True)
        print()


asyncio.run(main())
```

Recipe 會載入 Creature、宣告 channel，並接好 graph。Terrarium 自己不執行 LLM、也沒有推理迴圈；它負責的是結構 —— 拓樸、channel、lifecycle，以及在圖變化時跟著走的 session 記帳。

## 步驟 6 —— 使用 Studio 管理 session

目標：取得與 Web dashboard / API 相同的管理層：active sessions、saved sessions、catalog、identity、attach 與 editor flows。

```python
import asyncio

from kohakuterrarium import Studio


async def main() -> None:
    async with Studio() as studio:
        session = await studio.sessions.start_creature(
            "@kt-biome/creatures/general"
        )
        cid = session.creatures[0]["creature_id"]
        stream = await studio.sessions.chat.chat(
            session.session_id,
            cid,
            "Summarize the Studio layer in one sentence.",
        )
        async for chunk in stream:
            print(chunk, end="", flush=True)
        print()


asyncio.run(main())
```

當你是在寫應用 server、dashboard、editor 或多 session backend 時，`Studio` 通常是最方便的入口。

## 步驟 7 —— 什麼時候用組合代數

如果你只想把幾個 callable/agent 串成短 pipeline，不想手動管理長生命週期 graph，[組合代數](../concepts/python-native/composition-algebra.md) 提供 `>>`、`|`、`&`、`*` 操作子：sequence、fallback、parallel、retry。

## 你學到了什麼

- `Creature` 是由 `Terrarium` 托管的普通 Python 物件；`chat()` 會把它變成 async iterator。
- 底層 `Agent` 在你需要 `set_output_handler` 或直接事件控制時可用。
- `Terrarium` 以 graph topology 運行一隻或多隻 Creature。
- `Studio` 在引擎之上管理 active sessions、saved sessions、catalog、identity、attach policy 與 editor workflows。
- CLI 只是這些物件的一個使用者；你的應用也可以是另一個使用者。

## 延伸閱讀

- [Agent 作為 Python 物件](../concepts/python-native/agent-as-python-object.md) — 這個概念，以及它解鎖的模式。
- [程式化使用指南](../guides/programmatic-usage.md) — Python surface 的任務導向參考。
- [組合代數](../concepts/python-native/composition-algebra.md) — 把 agent 接成 Python pipeline 的操作子。
- [Python API 參考](../reference/python.md) — 精確簽名。
