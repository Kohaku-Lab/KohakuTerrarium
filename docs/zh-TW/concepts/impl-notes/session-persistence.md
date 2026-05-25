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

## 列表用 sidecar

`.kohakutr` 檔案是資料的來源，但用來「列表」的形狀不對 ——
當 `GET /api/sessions` 面對 1000 個 session 時，不應該為了渲染
側邊欄就打開 1000 個 SQLite 檔案。我們在上層加了一個 write-through
cache：

```
<session_dir>/.kt-index.kvault   ← 單一 SQLite 檔案，三張表：
    entries  (KVault)    filename → 打包後的 SessionIndexEntry
    search   (TextVault) 針對 name / preview / config_path /
                         agents / pwd 的 FTS5（用 BM25 排名）
    meta     (KVault)    schema_version、bootstrap_completed 等
```

一筆 `SessionIndexEntry` 是列表形狀的扁平 dict（`name`、
`last_active`、`status`、`config_type`、`node_id`、
`terrarium_name`、`preview`、`agents`、`parent_session_id`、
`forked_children`…），再加上從 `stat()` 拿到的 `(mtime, size)`
指紋。一隻 session 一列。冷列表現在只需要一次檔案開啟 + 一次表格
掃描，與 session 數量無關；搜尋則是單一 FTS5 查詢。

### 索引怎麼和磁碟保持一致

三條獨立路徑讓 entries 與磁碟上的檔案同步 —— 任一條都不是單點：

1. **Push hook**（`session_index/hooks.py`）。當 API server 自身
   擁有一個 `SessionStore` 時，`SessionIndexHook` 會訂閱其事件流，
   每 20 個事件或 5 秒（先到先發）就 upsert 一次條目。同一個 store
   既寫事件也更新索引 —— 不會落後。

2. **Pull reconcile**（`session_index/reconcile.py`）。走訪 session
   目錄，把每個檔案做指紋比對，只打開 `(mtime, size)` 和索引不同
   （或索引中尚未存在）的檔案，重新讀取它們的 meta 與 preview。
   已刪除的檔案會從索引剔除。這就是 API 在 `?refresh=true` 時
   觸發的路徑。`?full_rescan=true` 則強制重新讀取所有檔案 ——
   用在手動修改 `.kohakutr` 之後。

3. **Startup reconcile**。`get_session_index_default` 這個 singleton
   在每個 process 第一次打開時跑 reconcile。第一次開啟時跑 full
   reconcile（bootstrap）並設置 `bootstrap_completed` 旗標；之後的
   每次開啟（server 重啟）跑增量路徑，於是另一個 process 在 server
   關閉期間產出的 sessions（例如另一個 terminal 跑的 `kt run`）會被自動
   接進來。這裡若失敗會大聲 log，但絕不擋 server 啟動 —— 提供
   過期資料，仍比完全無法服務好。

### 為什麼要用 sidecar（而不是只放在記憶體）

- **可以撐過重啟。** 一個跑了很久的 server 即便崩在列表中途，下次
  開起來只要做指紋差異比對，毫秒級就能恢復，不需要重新打開 N 個檔案。
- **可以撐過搬家。** `mv ~/.kohakuterrarium/sessions /backup` 會把
  sidecar 一起帶過去；之後在 `/backup` 列表是瞬間完成。
- **一次打開、一次查詢。** 列出 1000 個 sessions 是一次 SQLite 打開
  + 一次 `ORDER BY last_active LIMIT 20`（或一次 FTS5 match）。
  沒有 sidecar 之前的路徑，光是渲染首頁都要打開 1000 個檔案。

Sidecar 是可以直接刪除的；下次呼叫 `get_session_index_default` 就會
重建。它沒有遷移路徑，因為裡面沒有任何不存在於 `.kohakutr` 來源
檔案中的狀態。

## 程式碼中的位置

- `src/kohakuterrarium/session/store.py` — `SessionStore` API。
- `src/kohakuterrarium/session/output.py` — `SessionOutput` 透過
  `OutputModule` 協定記錄事件，因此控制器層不需要特別處理。
- `src/kohakuterrarium/session/resume.py` — 重建路徑。
- `src/kohakuterrarium/session/memory.py` — FTS 與向量查詢。
- `src/kohakuterrarium/session/embedding.py` — embedding providers。
- `src/kohakuterrarium/studio/persistence/session_index/` — 列表用
  sidecar：`entry.py`（列結構）、`store.py`（KVault + TextVault 包裝）、
  `reconcile.py`（指紋差異 + 並行重讀）、`hooks.py`（來自執行中
  SessionStore 的 live push）、`__init__.py`（process 內 singleton +
  啟動 reconcile）。
- `src/kohakuterrarium/api/routes/persistence/saved.py` — HTTP 介面
  （`GET /api/sessions`、`DELETE /api/sessions/{name}`）。

## 另請參閱

- [記憶與壓縮](../modules/memory-and-compaction.md) — 概念層面的說明。
- [reference/cli.md — kt resume, kt search, kt embedding](../../reference/cli.md) — 使用者可見介面。
