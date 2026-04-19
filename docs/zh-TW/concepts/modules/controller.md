---
title: 控制器
summary: 從 LLM 串流、解析工具呼叫並派發回饋的推理迴圈。
tags:
  - concepts
  - module
  - controller
---

# 控制器

## 它是什麼

**控制器 (controller)** 是生物的推理迴圈。它從佇列取出事件，要求
LLM 回應，派發回傳的工具與子代理呼叫，收集結果，然後決定是否繼續
迴圈。

它*不是*「大腦」。大腦是 LLM。控制器是那層很薄的程式碼，負責讓
LLM 真的能在時間中持續工作。

## 為什麼它存在

LLM 是無狀態的：你餵它訊息，它吐回更多訊息。agent 是有狀態的：它
有正在執行的工具、被派生出的子代理、持續進來的事件、逐步累積的回
合。總得有某個東西把兩者橋接起來。

沒有控制器的話，一隻生物不是會坍縮成單次 LLM round-trip（聊天機器
人），就是每種 agent 設計都得自己寫一套膠水。控制器就是那個把
「LLM + 迴圈 + 工具」變成可重用底層，而不是一次性拼裝膠水的關鍵零件。

## 我們怎麼定義它

把控制器的契約簡化後，大致是：

```
loop:
    events = 從佇列收集（可堆疊事件做 batch，遇到不可堆疊事件就中斷）
    context = 從 events 建立這一回合的輸入
    stream = LLM.chat(messages + context)
    for chunk in stream:
        輸出文字 chunk
        派發解析出的 tool / sub-agent / framework-command 區塊
    等待 direct-mode 工具與子代理完成
    把它們的結果作為新事件餵回去
    繼續迴圈或結束
```

有三個設計選擇值得點名：

- **單一事件鎖。** 每隻生物同一時間只會跑一個 LLM 回合。觸發器可
  以自由觸發，但它們只會排進佇列，不會中斷當前回合。
- **可堆疊 batching。** 一陣相似事件突發時（例如同一個 tick 有兩個
  工具完成），會合併成同一回合。
- **工具在串流中途派發。** 控制器不會等 LLM 整段說完才觸發工具。
  見 [impl-notes/stream-parser](../impl-notes/stream-parser.md)。

## 我們怎麼實作它

主要類別是 `Controller`（`core/controller.py`）。它持有事件用的
`asyncio.Queue`、LLM 輸出串流的 parser 狀態機，以及對生物
`Registry`（工具）、`SubAgentManager`、`Executor` 與
`OutputRouter` 的參照。

關鍵不變條件：

- `_processing_lock` 會在整個「collect → stream → dispatch → await
  → loop」流程中持有。
- 不可堆疊事件（錯誤、優先訊號）會中斷當前 batch，自己拿到獨立回合。
- 控制器絕不直接呼叫工具；它會把工作交給 `Executor`，由後者產生
  `asyncio.Task`。

## 因此你可以做什麼

- **在工作階段中途切換 LLM。** `/model` 使用者指令或 `switch_model`
  API 會原地切換 LLM provider。控制器不在乎自己正在和哪個 provider
  對話。
- **動態 system prompt。** `update_system_prompt(...)` 可以在下一回
  合前追加或替換提示詞；控制器會自動接手使用。
- **重生某一回合。** `regenerate_last_response()` 會告訴控制器用當前
  狀態重新執行上一個 LLM 呼叫。
- **從任何地方注入事件。** 因為一切都經過事件佇列，plugin、工具，
  或外部 Python 程式都可以呼叫 `agent.inject_event(...)`，控制器會
  按順序處理它。

## 不要被它框住

沒有控制器的生物是說不通的 — 沒有迴圈就沒有 agent。但迴圈的*形狀*
是可以談的。plugin hook（`pre_llm_call`、`post_llm_call`、
`pre_tool_execute`、…）讓你能從外部重寫迴圈中的每一步，而不必碰
`Controller` 類別本身。見 [外掛](plugin.md)。

## 另見

- [組成一個 agent](../foundations/composing-an-agent.md) — 控制器位在什麼位置。
- [impl-notes/stream-parser](../impl-notes/stream-parser.md) — 為什麼工具會在 LLM 停下前就開始。
- [impl-notes/prompt-aggregation](../impl-notes/prompt-aggregation.md) — 控制器實際在驅動的是哪一份提示詞。
- [reference/python.md — Agent, Controller](../../reference/python.md) — 簽名。
