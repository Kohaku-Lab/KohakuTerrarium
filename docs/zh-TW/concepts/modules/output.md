---
title: 輸出
summary: 生物如何對外說話 — 將文字、活動與結構化事件扇出到各個 sink 的輸出路由器。
tags:
  - concepts
  - module
  - output
---

# 輸出

## 它是什麼

**輸出 (output)** 模組是生物回應其世界的方式。它接收控制器送出的所有內容 —
來自 LLM 的文字 chunk、工具開始 / 完成事件、活動通知、token 使用量更新 —
並把每一種內容路由到正確的 sink。

sink 可以不只一個。生物可以同時輸出到 stdout、串流到 TTS、推送到 Discord，
並且寫入檔案。

## 為什麼它存在

「把 LLM 回覆印到 stdout」只是最簡單的情況。真實部署還得回答一些簡單情況
不會碰到的問題：

- 當有三個 listener 時，串流中的 LLM chunk 要送去哪裡？
- 工具活動要走同一條流，還是另一條？
- 面向使用者的文字與面向日誌的文字，應不應該共用同一個 sink？
- 如果生物跑在 web UI 裡，究竟是誰在訂閱這些事件？

框架不想替每種介面各自特判，所以提供一個統一的 router，把每個 sink 都視為
具名輸出。

## 我們怎麼定義它

`OutputModule` 是一個非同步 consumer，具有像是
`on_text(chunk)`、`on_tool_start(...)`、`on_tool_complete(...)`、
`on_resume(events)`、`start()`、`stop()` 等方法。`OutputRouter`
持有一組這類模組 — 一個預設輸出，以及任意數量的 `named_outputs` —
並把事件扇出出去。

`controller_direct: true`（預設值）表示控制器的文字串流會直接流向預設輸出。
`controller_direct: false` 則允許你在中間插入處理器（rewriter、安全過濾器、
摘要器）。

## 我們怎麼實作它

內建輸出：

- **`stdout`** — 一般終端機輸出，可設定 prefix / suffix / stream-suffix。
- **`tts`** — 文字轉語音；後端包含 Fish、Edge、OpenAI，執行時自動選擇。
- **`tui`** — 當生物在 TUI 下執行時，使用 Textual 顯示。
- **（隱含）web streaming output** — 當生物跑在 HTTP/WebSocket server
  裡時使用。

`OutputRouter`（`modules/output/router.py`）也提供一條 activity stream，
供 TUI 與 HTTP client 顯示工具開始 / 完成事件，而不必把它們混進文字通道。

## 因此你可以做什麼

- **安靜的控制器，串流的子代理。** 把子代理標記為 `output_to: external` —
  它的文字會直接串流給使用者，而父控制器則維持內部運作。使用者會看到一段
  由專家型子代理組成的連貫回覆。
- **依用途分流 sink。** 把給使用者看的回答送到 stdout，把除錯筆記送到
  寫檔的 `logs` named output，把最終產物送到 Discord webhook。
- **後處理文字。** 設定 `controller_direct: false`，再加上一個自訂輸出，
  在控制器文字抵達使用者之前先清理、翻譯或加上浮水印。
- **與傳輸層無關的程式碼。** 同一隻生物可以跑在 CLI、web 或桌面環境，
  因為輸出層已把傳輸抽象化了。

## 不要被它框住

沒有輸出的生物也是合理的：有些 trigger 只會造成副作用（寫檔、寄 email）。
反過來說，輸出也可以是完整模組 — 一個 Python 模組甚至可以決定執行一個
迷你 agent，來選擇每個 chunk 應該如何格式化。這聽起來很誇張，而且通常也
確實如此，但它是可行的。

## 另見

- [子代理](sub-agent.md) — `output_to: external` 會直接經過 router 串流。
- [控制器](controller.md) — 真正餵資料給 router 的地方。
- [reference/builtins.md — Outputs](../../reference/builtins.md) — 內建列表。
- [guides/custom-modules.md](../../guides/custom-modules.md) — 如何撰寫你自己的模組。
