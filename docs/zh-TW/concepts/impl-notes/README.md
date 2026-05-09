---
title: 實作筆記
summary: 特定子系統實際運作細節的深度剖析 — 給貢獻者與好奇的讀者。
tags:
  - concepts
  - impl-notes
  - internals
---

# 實作筆記

這些文件不是使用者必讀；它們解釋的是**某些子系統實際上是怎麼寫的**，而不是怎麼用它。適合想貢獻框架或想搞清楚「為什麼這個設計是這個樣子」的讀者。

- [提示詞組合](prompt-aggregation.md) — system prompt 是怎麼從人格/提示詞、工具清單、框架 hint、按需載入的 skill 組出來的。
- [串流解析](stream-parser.md) — 用狀態機把 LLM 輸出解析成文字、工具呼叫、子代理派遣、框架指令。
- [非阻塞壓縮](non-blocking-compaction.md) — 控制器繼續跑的同時，summariser 在背景重建壓縮後的對話，切換點在回合之間。
- [工作階段持久化](session-persistence.md) — `.kohakutr` 檔案格式、每隻生物存什麼、恢復時怎麼重建對話狀態。
- [圖與 session](graph-and-sessions.md) — terrarium 引擎如何計算連通分量、在熱插拔呼叫下變更拓樸，並在自動合併 / 自動分裂中保持 session store 的一致。
