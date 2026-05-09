---
title: 為什麼是 KohakuTerrarium
summary: 每個 agent 產品都在重寫同一層基底設施，於是有了這個偏向 framework 的回應。
tags:
  - concepts
  - foundations
  - philosophy
---

# 為什麼 KohakuTerrarium 會存在

## 一個你大概已經觀察到的現象

過去兩年裡，出現了非常多 agent 產品：Claude Code、Codex、OpenClaw、Gemini CLI、Hermes Agent、OpenCode，還有很多很多。它們彼此都真的不一樣：不同的工具介面、不同的控制器迴圈、不同的記憶策略、不同的多代理設計。

但它們也都從零開始重做同一層基底：

- 一個會從 LLM 串流並解析工具呼叫的控制器
- 一層工具註冊表與派發層
- 處理 `/loop`、背景工作、idle check 的觸發器系統
- 一個為了上下文隔離而設計的子代理機制
- 一個或多個互動介面的輸入與輸出 plumbing
- session、持久化、resume
- 某種形式的多代理 wiring

每個團隊只要想嘗試一種新的 agent 形狀，最後都得把這些東西再蓋一次。這代表大量程式碼都花在重寫，只為了走到真正有趣的部分：*新的設計本身*。

## 常見的逃法，以及它為什麼會失敗

最常見的回應是：「做一個夠泛化的 agent，讓它處理所有情況。」但這條路會撞上懸崖：你涵蓋的形狀越多，就得加越多特例；特例越多，這個通用 agent 就越脆弱。一年後有人又有了新想法，結果發現這個通用 agent 裝不進去，於是大家重新開始。

把「通用」建立在單一產品上，是一次失敗的最佳化。

## 真正的動作

讓 **打造一隻目的明確的 agent 變得便宜**。

如果每一種新的 agent 形狀，只需要一份設定檔、幾個自訂模組，以及一個清楚的心智模型，這個領域就不會一直重造輪子。那層基底——每個 agent 都需要，而且彼此幾乎一樣的部分——就可以集中留在同一個地方。真正新的部分，才是你自己去寫的。

那層基底就是 KohakuTerrarium：一個 **給 agents 用的 framework**，而不是另一個 agent。

## 這裡的「基底」是什麼意思

給一份具體清單方便校準：

- 一套統一的事件模型。使用者輸入、計時器觸發、工具完成、頻道訊息——全都用同一種信封。
- 六模組生物抽象。參見 [what-is-an-agent](what-is-an-agent.md)。
- 一層 session 系統，同時負責執行期持久化與可搜尋的知識庫。
- 一個多代理執行期引擎（terrarium），托管生物、擁有頻道圖與拓樸記帳，但不執行自己的 LLM。
- 一個管理框架（studio），處理 catalog、identity、session 生命週期、persistence 與 editor，讓每一個 UI / CLI / API 共用同一份實作。
- Python-native 的組合方式：每個模組都是 Python class，每個 agent 都是一個 async Python value。
- 開箱即用的執行期介面（CLI、TUI、HTTP、WebSocket、desktop、daemon），讓你不用自己寫 transport code。

這些都是你在想試一種新 agent 設計時，不會想重蓋的部分。

## KohakuTerrarium 不是什麼

- **不是 agent 產品。** 你不會「執行 KohakuTerrarium」；你會執行一隻用它建出來的生物。如果你想先試用現成生物，展示用的是 [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome) 套件。
- **不是 workflow engine。** 這裡沒有任何地方假設你的 agent 會照固定步驟序列前進。
- **不是通用 LLM wrapper。** 它不打算變成那個樣子。

## 用一句話定位

> KohakuTerrarium 是一台拿來打造 agents 的機器，讓人們在每次想做新 agent 時，不必都重新發明這台機器。

## 延伸閱讀

- [什麼是 agent](what-is-an-agent.md) — 這個 framework 所圍繞的定義。
- [邊界](../boundaries.md) — 什麼情況 KT 適合，什麼情況不適合。
- [kt-biome](https://github.com/Kohaku-Lab/kt-biome) — 展示用生物與 plugin 套件。
