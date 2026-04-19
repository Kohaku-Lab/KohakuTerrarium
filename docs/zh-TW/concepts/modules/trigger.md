---
title: 觸發器
summary: 任何在沒有明確使用者輸入時喚醒控制器的東西——計時器、閒置、頻道、webhook、監看器。
tags:
  - concepts
  - module
  - trigger
---

# 觸發器

## 它是什麼

**觸發器 (trigger)** 是任何在沒有明確使用者輸入時喚醒控制器的東西。計時器、閒置偵測器、webhook 接收器、頻道監聽器，以及監看條件，全部都屬於 trigger。每個 trigger 都會作為背景作業執行，並在觸發條件成立時把 `TriggerEvent` 推進事件佇列。

## 為什麼它存在

純粹由輸入驅動的 agent，只有在使用者出現時才能工作。但真實的 agent 需要：

- 在沒人盯著時執行 `/loop` 風格的週期性計畫；
- 回應另一隻生物送來的頻道訊息；
- 在最後一個事件發生後 N 秒醒來做摘要；
- 接收來自外部服務的 webhook；
- 輪詢某個資源，並在條件翻轉時觸發。

你可以把這些各自用臨時程式碼硬接上去。這個框架的看法是：它們其實全都是同一種東西——事件來源——所以值得共享同一個抽象。

## 我們怎麼定義它

一個 trigger 會實作：

- 一個會產出 `TriggerEvent` 的非同步 generator `fire()`；
- `to_resume_dict()` / `from_resume_dict()`，讓 trigger 能跨工作階段保存與恢復；
- 一個 `trigger_id` 供定址使用（讓工具可以列出 / 取消它）。

trigger manager 會為每個已註冊 trigger 啟動一個背景作業。每個作業都會反覆迭代 `fire()` 並推送事件。

## 我們怎麼實作它

內建 trigger 類型：

- **`timer`** —— 每 N 秒觸發一次，或依 cron 排程觸發。
- **`idle`** —— 若 N 秒內沒有任何事件就觸發。
- **`channel`** —— 監聽具名頻道；收到訊息時觸發。
- **`webhook` / `http`** —— 接收 POST 請求。
- **`monitor`** —— 當某個對 scratchpad / context 的 predicate 回傳 true 時觸發。

接收端常見的 `TriggerEvent` 類型有：`user_input`（來自 input 模組）、`timer`、`channel_message`（來自 channel trigger）、`tool_complete`、`subagent_output`、`creature_output`（另一隻生物透過 `output_wiring` 在回合結束時送出的輸出——這是框架自動發出的，不是由模組觸發），以及 `error`。

`TriggerManager`（`core/trigger_manager.py`）擁有這些執行中的作業，會把完成結果接回 agent 的事件 callback，並把 trigger 狀態持久化到工作階段儲存中，讓 `kt resume` 可以重新建立它們。

設定期的 trigger 宣告在 `config.triggers[]`。執行期 trigger 也可以由 agent 自己安裝——每個通用 trigger 類別（`universal = True` + `setup_*` metadata）都會被包成它自己的工具（`add_timer`、`watch_channel`、`add_schedule`），生物可在 `tools: [{ name: add_timer, type: trigger }]` 下列出它——也能透過 `agent.add_trigger(...)` 以程式方式安裝。

## 因此你可以做什麼

- **週期性 agent。** 每小時觸發一次的 `timer`，可以讓某隻生物定期重新整理它對檔案系統或某組指標的觀察。
- **跨生物接線。** `channel` trigger 是讓以頻道為基礎的生態瓶通訊成立的機制。對於確定性的 pipeline 邊，框架也會在生物宣告 `output_wiring` 時，於回合結束發出 `creature_output` 事件——見 [生態瓶](../multi-agent/terrarium.md)。
- **由閒置驅動的摘要。** 一個在安靜兩分鐘後觸發的 `idle` trigger，可以派遣 `summarize` 子代理，並把結果送到某個日誌頻道。
- **外部訊號。** `webhook` trigger 可以把生物變成 CI hook、部署事件或上游產品流量的接收者。
- **自適應監看器。** 某個自訂 trigger 的 `fire()` 若內部跑一隻小型巢狀 agent，就能依據判斷而不是固定規則來決定*何時*喚醒外層生物。參見 [patterns](../patterns.md)。

## 不要被它框住

一隻生物可以沒有任何 trigger。也可以只有 trigger（沒有 input）。框架不替這些配置排高低，只是全部都支援。而且因為 trigger 本身就是一個 Python 物件，你完全可以把一隻 agent 塞進去——做出一個會*思考*是否該觸發，而不是照手寫規則執行的 watcher。這種模式讓「具 agent 特性的環境式行為」變得很便宜。

## 另見

- [輸入](input.md) —— 使用者內容這個特殊案例的 trigger。
- [頻道](channel.md) —— 支撐多代理通訊的那種 trigger。
- [reference/builtins.md — Triggers](../../reference/builtins.md) —— 完整清單。
- [patterns.md —— adaptive watcher](../patterns.md) —— agent-inside-trigger。
