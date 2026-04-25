---
title: 輸入
summary: 將使用者訊息帶進事件佇列的特化觸發器。
tags:
  - concepts
  - module
  - input
---

# 輸入

## 它是什麼

**輸入 (input)** 模組是外部世界把工作交給生物的方式。在正典推導中，
它位於控制器之前，負責觸發第一個事件。實務上，它只是一種特定型態
的觸發器 — 依慣例被標記為「使用者輸入」的那一種。

## 為什麼它存在

如果一隻生物只能回應環境中的觸發器（timer、channel、webhook），
那你就沒辦法和它聊天。大多數 agent 至少有時會在人類參與的迴圈中運作，
而那個人類需要一個可以輸入文字的地方。

## 我們怎麼定義它

`InputModule` 實作一個非同步方法 `get_input()`，它會阻塞直到某個
`TriggerEvent` 準備好。它回傳的任何東西，都會像 timer 觸發或 channel
訊息一樣，被推進事件佇列。

這也是為什麼文件一直說「input 也是 trigger」— 從結構上來看確實如此。
兩者的差異主要在生命週期（input 通常在前景，trigger 通常在背景）
以及意圖（input 承載的是使用者內容）。

## 我們怎麼實作它

內建輸入模組：

- **`cli`** — 由 `prompt_toolkit` 驅動的行編輯器。支援歷史紀錄、
  slash commands、多行輸入與貼上。
- **`cli_nonblocking`** — 和 `cli` 表面相近，但會在每次按鍵之間把控制權還給
  event loop，讓 trigger 在輸入過程中也能觸發。
- **`tui`** — 當生物在 Textual 下執行時，TUI composer 就是輸入來源。
- **`none`** — 永遠不產生事件的 stub；給純 trigger 驅動的生物使用。

音訊/ASR 實作不會由核心套件匯入。請參考 `examples/agent-apps/conversational/custom/` 底下 opt-in 的 ASR 與 Whisper 輸入模組，並透過 `type: custom` 載入。

自訂輸入可透過生物設定中的 `type: custom` 或 `type: package`
註冊。它們必須實作 `InputModule`，並由 `bootstrap/io.py` 載入。

## 因此你可以做什麼

- **純 trigger 生物。** `input: { type: none }` 加上一個或多個
  trigger：cron 生物、channel watcher、webhook receiver。
- **多介面聊天。** 由 HTTP 驅動的部署不需要 CLI 輸入 —
  `AgentSession` transport 可以透過 `inject_input()` 以程式方式推送
  使用者內容。
- **感測器式輸入。** 接上檔案系統監看器、Discord listener，或 MQTT
  consumer。生物本身不會知道差別。
- **把輸入當成策略層。** 輸入模組可以在內容抵達控制器之前先轉換
  使用者輸入 — 翻譯語言、做 moderation 檢查、移除秘密資訊。

## 不要被它框住

輸入是可選的。沒有「人類坐在終端機前」的 Discord bot 生物，可以完全
省略 input，改由 HTTP WebSocket trigger 驅動自己。反過來說，一隻生物
也可以同時有多個有效輸入介面 — 使用者能在 CLI 打字，同時 webhook 在推
事件，timer 也能一起觸發。

## 另見

- [觸發器](trigger.md) — 一般情況；input 只是它的特定形狀。
- [reference/builtins.md — Inputs](../../reference/builtins.md) — 內建輸入模組完整列表。
- [guides/custom-modules.md](../../guides/custom-modules.md) — 如何寫你自己的輸入。
