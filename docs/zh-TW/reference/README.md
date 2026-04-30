---
title: 參考
summary: 完整規格 — 每一個欄位、指令、端點、hook、Python 入口點。
tags:
  - reference
  - overview
---

# 參考

參考是給「我知道我要找什麼，只要告訴我確切的樣子」的讀者看的。不解釋為什麼，不示範用法；那是[使用指南](../guides/README.md) 與[核心概念](../concepts/README.md)的職責。

## 章節

- [CLI](cli.md) — 每一個 `kt` 子指令 (run、resume、login、install、list、info、model、embedding、search、terrarium、serve、app…)。
- [設定檔](configuration.md) — 生物、生態瓶、LLM 設定檔、MCP 伺服器、上下文壓縮、外掛、輸出接線的所有欄位。
- [內建模組](builtins.md) — 內建的工具、子代理、觸發器、輸入、輸出的參數、行為與預設值。
- [Python API](python.md) — `kohakuterrarium` 套件的公開介面：`Terrarium`、`Creature`、`Studio`、底層 `Agent`、`compose`、測試 helper。
- [外掛 hook](plugin-hooks.md) — 外掛可以註冊的每一個 lifecycle hook、觸發時機、payload 內容。
- [HTTP API](http.md) — `kt serve` 的 REST 端點與 WebSocket 通道，附 request / response 結構。
- [v1.3.0 發布說明](release-notes-1.3.0.md) — 最終 changelog、相容性說明、發布流程與驗證摘要。
