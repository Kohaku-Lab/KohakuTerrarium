---
title: 使用指南
summary: 任務導向的 how-to 文件：撰寫生物、把它們組合起來、部署 agent。
tags:
  - guides
  - overview
---

# 使用指南

指南是任務導向的 how-to。每一份指南回答一個具體問題 — 「我該怎麼設定繼承？」、「我該怎麼把記憶加到生物身上？」、「這個生物怎麼上線？」。

和教學不同，指南不會從零開始；它假設你已經有一個可以動的 agent，現在要加功能或調整行為。
和參考也不同，指南的目的是「教你怎麼做出好的選擇」，而不是窮舉所有欄位。

## 入門

- [快速開始](getting-started.md) — 安裝框架、安裝 kt-biome、跑起一個 agent。

## 撰寫

- [設定檔](configuration.md) — 生物設定的結構、繼承、prompt 鏈、日常會用到的欄位。
- [撰寫生物](creatures.md) — 提示詞設計、工具與子代理挑選、LLM 設定檔、發佈生物供他人重用。
- [生態瓶](terrariums.md) — 橫向多代理，頻道、輸出接線、特權節點、熱插拔、觀察。
- [組合代數](composition.md) — 用 Python 的 `>>`、`&`、`|`、`*` 把 agent 與 async callable 串起來。
- [程式化使用](programmatic-usage.md) — 在你自己的 Python 程式碼裡驅動 `Terrarium`、`Creature`、`Studio` 與底層 `Agent`。

## 存續

- [工作階段與恢復](sessions.md) — `.kohakutr` 檔案怎麼運作、如何恢復生物、怎麼重播對話歷史。
- [記憶](memory.md) — 工作階段上的 FTS5 + vector 搜尋、embedding 提供者、檢索模式。

## 擴充

- [外掛](plugins.md) — prompt 外掛與 lifecycle 外掛的用法、組合、時機。
- [子代理](sub-agents.md) — 內建與內聯專家、執行期預算外掛和自動壓縮。
- [自訂模組](custom-modules.md) — 自訂輸入、觸發器、工具、輸出、子代理的寫法與註冊。
- [MCP](mcp.md) — 連接 Model Context Protocol 伺服器，把它們的工具暴露給生物。

## 發佈與部署

- [套件](packages.md) — 透過 `kt install` 安裝、`kohaku.yaml` manifest、`@pkg/` 參照、發佈自己的套件。
- [Serving](serving.md) — `kt serve` 提供的 HTTP API + WebSocket + 網頁 dashboard、`kt app` 提供桌面版。
- [前端版面](frontend-layout.md) — Vue 3 dashboard 的組織方式、在哪裡擴充、事件從後端流到 UI 的路徑。
- [範例](examples.md) — 內附範例生物、生態瓶、程式碼的導覽 — 先看哪個、為什麼。
