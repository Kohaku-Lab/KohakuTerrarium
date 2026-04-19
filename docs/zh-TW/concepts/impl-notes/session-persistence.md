---
title: 工作階段持久化
summary: 說明 .kohakutr 檔案格式、每個生物會儲存哪些內容，以及 resume 如何重建對話狀態。
tags:
  - concepts
  - impl-notes
  - persistence
---

# 工作階段持久化

## 這要解決的問題

一個生物的歷史資料有三個消費者，而且需求各不相同：

1. **Resume。** 發生崩潰後（或執行 `kt resume --last` 時），我們需要
   快速重建代理狀態。因此我們希望序列化的內容盡可能精簡。
2. **人類搜尋。** 使用者執行 `kt search <session> <query>` 時，
   會期待能針對所有細節進行關鍵字 + 語意搜尋。
3. **代理端 RAG。** 執行中的代理在一個輪次內呼叫 `search_memory` 時，
   也會期待同樣的能力。

單一儲存層必須同時服務這三種用途。若資料形狀選錯，至少其中一種
就會變得昂貴，甚至不可行。

## 曾考慮的方案

- **僅儲存對話記錄。** Resume 很便宜；搜尋很糟糕
  （沒有工具活動、沒有 trigger 觸發、沒有子代理輸出）。
- **只有完整事件日誌，沒有快照。** 搜尋很好；resume 很慢
  （必須重播所有事件）。
- **只有快照。** Resume 很快；但沒有可搜尋的歷史。
- **雙重儲存：append-only 事件日誌 + 每輪對話快照。** 這就是我們的做法。

## 我們實際怎麼做

`.kohakutr` 檔案是一個 SQLite 資料庫（透過 KohakuVault 管理），
其中包含下列表格：

- `events` — 每個事件的 append-only 日誌（文字區塊、工具呼叫、
  工具結果、trigger 觸發、頻道訊息、token 使用量）。永不改寫。
- `conversation` — 每個（agent、輪次邊界）對應一列快照，
  儲存訊息列表（透過 msgpack，可保留 tool-call 結構）。
- `state` — 草稿區與各 agent 的計數器。
- `channels` — 頻道訊息歷史。
- `subagents` — 已生成子代理的對話快照，會在銷毀前儲存。
- `jobs` — 工具／子代理執行紀錄（狀態、參數、結果）。
- `meta` — 工作階段中繼資料、設定檔路徑、執行識別資訊。
- `fts` — 建立在 events 上的 SQLite FTS5 索引（關鍵字搜尋）。
- 向量索引（選用，位於同一個 store 中）— 在需要時由
  `kt embedding` 建立。

### Resume 路徑

1. 載入 `meta` → 取得 session id、config path、生物清單。
2. 載入 `conversation[agent]` 快照 → 重建 agent 的
   `Conversation` 物件。
3. 載入 `state[agent]:*` → 還原草稿區。
4. 載入 `type == "trigger_state"` 的 events → 透過
   `from_resume_dict` 重新建立 triggers。
5. 將事件重播給 output module 的 `on_resume` → 為 TTY 使用者
   重繪 scrollback。
6. 載入 `subagents[parent:name:run]` → 重新接回子代理對話。

### 搜尋路徑

- FTS 模式：`events` FTS5 比對 → 依順序回傳區塊。
- 語意模式：向量搜尋 → 找出最近的事件。
- 混合模式：進行 rank-fuse。
- 自動模式：若向量存在則用語意搜尋，否則用 FTS。

### 代理端 RAG

內建工具 `search_memory` 會呼叫與 CLI 相同的搜尋層；若有要求，
可依 agent 名稱過濾；再截斷命中結果，並將它們作為工具結果回傳。

## 維持不變的條件

- **事件不可變。** 它們只會被追加。
- **快照以每輪為單位。** 不是每個事件一份。Resume 相對於快照是 O(1)，
  而不是相對於整段歷史的 O(N)。
- **不可序列化的狀態會從 config 重建。** 像 sockets、pywebview
  handles、LLM provider sessions —— 都是重新建立，而不是還原。
- **每個工作階段一個檔案。** 可攜、可複製；`.kohakutr` 副檔名也讓工具
  能辨識它。
- **Resume 可選擇停用。** `--no-session` 會完全停用這個 store。

## 程式碼中的位置

- `src/kohakuterrarium/session/store.py` — `SessionStore` API。
- `src/kohakuterrarium/session/output.py` — `SessionOutput` 透過
  `OutputModule` 協定記錄事件，因此控制器層不需要特別處理。
- `src/kohakuterrarium/session/resume.py` — 重建路徑。
- `src/kohakuterrarium/session/memory.py` — FTS 與向量查詢。
- `src/kohakuterrarium/session/embedding.py` — embedding providers。

## 另請參閱

- [記憶與壓縮](../modules/memory-and-compaction.md) — 概念層面的說明。
- [reference/cli.md — kt resume, kt search, kt embedding](../../reference/cli.md) — 使用者可見介面。
