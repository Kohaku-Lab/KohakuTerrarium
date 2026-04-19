---
title: 記憶與壓縮
summary: 工作階段儲存如何同時成為可搜尋的記憶，以及非阻塞壓縮如何把上下文維持在預算內。
tags:
  - concepts
  - memory
  - compaction
---

# 記憶與壓縮

## 它是什麼

這裡其實是兩個彼此相關的系統：

- **記憶。** `.kohakutr` 工作階段檔同時扮演執行期持久化與可搜尋知識庫。
  每個事件都會建立索引，用於全文搜尋（FTS5），也可選擇建立向量搜尋。
  Agent 可以透過 `search_memory` 工具，從內部查詢這些內容。
- **壓縮。** 長時間執行的生物終究會撐爆上下文視窗。自動壓縮會在背景
  摘要舊回合，而且不會暫停控制器，因此 agent 能一邊繼續工作，一邊把
  過去內容壓縮得更精簡。

這其實是同一個問題的兩面：*我們要怎麼處理生物累積下來的歷史？*

## 為什麼它存在

### 記憶

多數 agent 框架把歷史當成暫時性的東西：它只服務當前的 LLM 呼叫，
也許會為了「resume」而持久化，其餘情況下就消失了。這會丟掉大量訊號。
同一份事件紀錄其實可以同時支援：

- `kt resume`（在工作中途重建 agent）、
- `kt search`（讓人類查看發生過什麼）、
- agent 對自己歷史做 RAG（`search_memory`）。

一個儲存，三個消費者。

### 壓縮

上下文視窗雖然持續變大，但永遠追不上需求增長。沒有壓縮的話，跑了幾小時
的生物最後一定會撞牆。天真的壓縮方式會在摘要期間直接暫停 agent — 在
agent 框架裡，這等於「控制器卡住，等待 50k token 被濃縮成 2k」。
對 ambient agents 來說，這是不能接受的。

非阻塞壓縮會在背景 task 裡完成摘要，並在回合與回合之間以原子方式把結果
接回去。控制器本身不會停下來。

## 我們怎麼定義它

### 工作階段儲存的形狀

`.kohakutr` 是一個 SQLite 檔案（透過 KohakuVault），裡面有以下資料表：

- `meta` — 工作階段中繼資料、快照、設定
- `events` — append-only 事件日誌
- `state` — scratchpad、計數器、每個 agent 的狀態
- `channels` — 訊息歷史
- `conversation` — 供快速 resume 使用的最新快照
- `subagents` — 子代理的對話快照
- `jobs` — 工具 / 子代理執行紀錄
- `fts` — 事件的全文索引
- （向量索引，可選，只有建立 embeddings 時才有）

### 壓縮契約

生物有一個 `compact` 設定區塊，包含：`enabled`、`max_tokens`
（或自動推導）、`threshold`（到達多少預算百分比時開始壓縮）、
`target`（壓縮後降到多少百分比）、`keep_recent_turns`
（永不摘要的活躍區），以及可選的 `compact_model`
（更便宜的摘要模型）。

每回合結束時，如果 `prompt_tokens >= threshold * max_tokens`，
compact manager 就會啟動一個背景 task。

## 我們怎麼實作它

- `session/store.py` — 以 KohakuVault 為後端的持久化儲存。
- `session/output.py` — 負責寫入事件的 output consumer。
- `session/resume.py` — 把資料重播進新建好的 agent。
- `session/memory.py` — FTS5 查詢與向量搜尋。
- `session/embedding.py` — model2vec / sentence-transformer / API
  provider 的 embeddings。
- `core/compact.py` — 使用 atomic-splice 技巧的 `CompactManager`。
  見 [impl-notes/non-blocking-compaction](../impl-notes/non-blocking-compaction.md)。

Embedding provider（`kt embedding`）：

- **model2vec**（預設，不需要 torch；預設組合包含 `@tiny`、
  `@best`、`@multilingual-best` 等）
- **sentence-transformer**（需要 torch）
- **api**（外部 embedding 端點，例如 jina-v5-nano）

## 因此你可以做什麼

- **從任何地方恢復。** `kt resume` / `kt resume --last` 可以接回數小時前
  被中斷的工作階段。
- **搜尋工作階段。** `kt search <session> <query>` — 支援 FTS、語意、
  hybrid 或自動偵測模式。
- **agent 端 RAG。** agent 在回合中呼叫 `search_memory`，取回相關過去事件，
  然後帶著這些上下文繼續。
- **長時間 ambient 執行。** 一隻連跑數天的生物不會撞上上下文牆：壓縮會讓
  滾動摘要一直維持在最新 N 個回合之上。
- **跨工作階段記憶。** 更進階的設定可以從 config 拉出 session store 路徑，
  讓相關生物共用同一份儲存。

## 不要被它框住

工作階段持久化是 opt-out（`--no-session`）。embeddings 是 opt-in。
壓縮則是每隻生物各自 opt-out。生物完全可以不使用這些功能 — 記憶是方便性，
不是必要條件。

## 另見

- [impl-notes/session-persistence](../impl-notes/session-persistence.md) — 雙儲存細節。
- [impl-notes/non-blocking-compaction](../impl-notes/non-blocking-compaction.md) — atomic-splice 演算法。
- [reference/cli.md — kt embedding, kt search, kt resume](../../reference/cli.md) — 指令介面。
- [guides/memory.md](../../guides/memory.md) — 實作指南。
