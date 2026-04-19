---
title: 開發
summary: 給貢獻者看的文件 — 內部結構、相依圖、前端、測試策略。
tags:
  - dev
  - overview
---

# 開發

給框架本身貢獻者 (不是使用者) 的文件。如果你只是想用 KohakuTerrarium 跑 agent，請回到[使用指南](../guides/README.md)。

## 章節

- [內部結構](internals.md) — 執行期的整體組裝：事件佇列、控制器迴圈、executor、子代理管理、外掛包裝。
- [相依圖](dependency-graph.md) — 模組 import 方向的不變式與強制它們的測試。
- [前端](frontend.md) — Vue 3 dashboard 的版面、狀態 store、WebSocket 連接，以及如何貢獻 UI 變更。
- [測試](testing.md) — 測試的目錄結構、`ScriptedLLM` 與 `TestAgentBuilder` helper、如何寫出具決定性的 agent 測試。

## 專案治理

- 貢獻流程：Code of Conduct、CONTRIBUTING 指引都在倉庫根目錄。
- 發布節奏：參見 [ROADMAP](https://github.com/Kohaku-Lab/KohakuTerrarium/blob/main/ROADMAP.md) 了解已完成與正在探索的方向。
