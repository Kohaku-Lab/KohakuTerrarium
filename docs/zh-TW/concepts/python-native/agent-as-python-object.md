---
title: Agent 作為 Python 物件
summary: 為什麼每個 agent 都是 Python 物件、這樣會解鎖什麼能力，以及嵌入式使用和執行 CLI 有何不同。
tags:
  - concepts
  - python
  - embedding
---

# Agent 作為 Python 物件

## 它是什麼

在 KohakuTerrarium 裡，agent 不是一份設定檔——設定檔只是描述它。主要的公開執行期句柄是一隻運行中的 `Creature`：一個由 `Terrarium` 引擎托管的 async Python 物件。子代理是同一套 Agent runtime 在父 Creature 內部的巢狀實例。`Studio` 是引擎之上的管理 facade，負責 catalog、identity、active sessions、persistence、attach 與 editor 流程。更底層的 `kohakuterrarium.core.agent.Agent` 仍然存在，用於進階事件 / 輸出控制。

所有東西都可以被呼叫、被 await、被組合。

## 為什麼這很重要

大多數 agent 系統都暴露兩層：

1. 一層設定層（YAML、JSON）來描述「這隻 agent」。
2. 一個 runtime（通常是 server 或 CLI）去讀設定並產生行為。

而你真正想建立在上面的行為，通常又得放進第三層——另一個 process、另一個 container、另一套 plugin system。為了做一件其實可以只是函式呼叫的事情，卻多了很多跳躍。

KohakuTerrarium 把這些層折疊起來：你可以直接 `import kohakuterrarium`、載入設定、啟動 Creature、呼叫它，並且任意處理它吐出的事件。agent 是一個 value；value 可以放進其他 value 裡。

## 關鍵介面長什麼樣子

應用程式碼優先從引擎層的 `Creature` 句柄開始。它有圖成員身份，也有 streaming chat：

```python
from kohakuterrarium import Terrarium

engine, creature = await Terrarium.with_creature("@kt-biome/creatures/swe")
try:
    async for chunk in creature.chat("What does this do?"):
        print(chunk, end="")
finally:
    await engine.shutdown()
```

Terrarium recipe 也是同樣形狀：

```python
from kohakuterrarium import Terrarium

async with await Terrarium.from_recipe("@kt-biome/terrariums/swe_team") as engine:
    swe = engine["swe"]
    async for chunk in swe.chat("Fix the auth bug."):
        print(chunk, end="")
```

當你還需要 catalog / settings / session / persistence 政策時，用 `Studio`：

```python
from kohakuterrarium import Studio

async with Studio() as studio:
    session = await studio.sessions.start_creature("@kt-biome/creatures/general")
    print(session.session_id)
```

只有在需要直接注入事件、自訂 output handler 或其它底層控制時，才下探到 `Agent`：

```python
from kohakuterrarium.core.agent import Agent

agent = Agent.from_path("@kt-biome/creatures/swe")
agent.set_output_handler(lambda text: print(text, end=""), replace_default=True)

await agent.start()
await agent.inject_input("Explain what this codebase does.")
await agent.stop()
```

## 因此你可以做什麼

真正的回報不是「agent 是 Python」——而是「因為 agent 是 Python，而模組也是 Python，所以你可以把 agent 放進任何模組裡」。幾個具體 pattern：

### Plugin 裡放 agent（智慧護欄）

做一個 `pre_tool_execute` plugin，實作內容是跑一隻小型巢狀 agent 來判斷是否允許工具呼叫。外層 Creature 的主對話可以保持乾淨；護欄自己的推理在自己的上下文裡完成。

### Plugin 裡放 agent（無縫記憶）

一個 `pre_llm_call` plugin 先跑一隻很小的 retrieval agent，去搜尋 session 的事件日誌（或外部向量資料庫），挑出相關的過去內容，然後把它注入 LLM 訊息。從外層 Creature 的角度看，它的記憶只是「自然地變好了」。

### Trigger 裡放 agent（自適應觀察者）

不是寫 `timer: 60s`，而是做一個自訂 trigger，在 `fire()` 本體裡每次 tick 都跑一隻小 agent。這隻 agent 會看目前狀態，決定是否該喚醒外層 Creature。這種環境感知式智慧，不需要依賴固定規則。

### Tool 裡放 agent（上下文隔離的專家）

做一個工具，呼叫時會 spawn 一隻全新的 agent 來完成工作。對 LLM 來說，它呼叫這個工具的方式跟其他工具完全一樣；但從實作面看，這個工具本身就是一整套子系統。當你需要完全隔離的子系統——不同模型、不同工具、不同 prompt——這就很好用。

### Output 模組裡放 agent（路由接待員）

做一個 output 模組，專門決定每一段文字該送去哪裡。簡單規則可以用 switch statement；如果路由判斷很細膩，就接一隻 agent 進來讀串流並做決策。

## 這讓哪些交叉引用成為可能

[patterns](../patterns.md) 文件會用最小片段把這些做法逐一寫開。這份概念文件存在的目的，是要講清楚：*這些都不是特殊技巧*。它們只是「agent 是第一等 Python value」的直接應用。

## 不要被邊界綁住

你不一定要用 Python 來建立 Creature——在大多數情況下，只靠設定檔就夠了。但如果某份 Creature 設定撞上牆，讓你開始想要「在 agent 正在執行的一個步驟裡，再放進一隻會做判斷的 agent」，那個 Python 基底其實早就在那裡了，不需要再發明一套新的 plugin system。

## 延伸閱讀

- [Composition algebra](composition-algebra.md) — 給 Python 端 pipeline 用的操作子。
- [Patterns](../patterns.md) — 這些能力解鎖出的意外用法。
- [guides/programmatic-usage.md](../../guides/programmatic-usage.md) — 這一頁的任務導向版本。
- [reference/python.md](../../reference/python.md) — 簽章與 API 索引。
