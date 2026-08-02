---
title: 工作階段與恢復
summary: .kohakutr 工作階段檔怎麼運作、怎麼恢復一隻生物，以及怎麼重播對話歷程。
tags:
  - guides
  - session
  - persistence
---

# 工作階段

寫給想持久化、恢復或封存 agent 執行紀錄的讀者。

工作階段 (session) 把一次執行的操作狀態（對話、事件、子代理對話、頻道歷史、scratchpad、job、可恢復的觸發器、設定 metadata）全部收進一個 `.kohakutr` 檔案。你可以在任何時間點停掉一隻生物 (creature)，之後從同一個地方原封不動接回來。

概念入門：[記憶與壓縮](../concepts/modules/memory-and-compaction.md)、[工作階段與環境](../concepts/modules/session-and-environment.md)。

### 壓縮後的編輯與重新產生

壓縮會改變即時提示並寫入快速恢復快照，但不會刪除追加式事件日誌。Studio 使用持久化的 `event_id`、`turn_index` 和 `branch_id` 定位可編輯的使用者訊息。儲存並重新執行或重新產生時，新分支會從所選訊息之前的原始事件前綴重建，並忽略壓縮摘要與快照；舊分支及其後續事件仍可切換與恢復。定位缺失、歧義、指向回合中注入輸入或與所選分支衝突時，操作會在不修改歷程的情況下失敗。

## `.kohakutr` 檔案

`.kohakutr` 是一個 SQLite 資料庫 (透過 KohakuVault)，有九張表：

| 表 | 用途 |
|---|---|
| `meta` | 工作階段 metadata、設定快照、terrarium 拓樸 |
| `state` | 各 agent 的 scratchpad、輪次計數、累積 token 用量、可恢復的觸發器 |
| `events` | append-only 日誌：每個文字 chunk、工具呼叫、觸發、token 用量事件 |
| `channels` | 以頻道名稱為 key 的頻道訊息歷史 |
| `subagents` | 以 parent + name + run 為 key 的子代理對話快照 |
| `jobs` | 工具與子代理的 job 紀錄 |
| `conversation` | 各 agent 最新的對話快照 (為了快速恢復) |
| `fts` | events 的 FTS5 索引 (給 `kt search` 用) |
| `vectors` | 選用的 embedding 欄位 (由 `kt embedding` 填入) |

事件資料是 append-only 的，版本管理透過 KohakuVault 的 auto-pack。二進位產物可以放在旁邊的 `<session>.artifacts/` 目錄，所以如果一次執行產生了圖片或其他二進位輸出，封存時要把 `.kohakutr` 檔和它的 artifacts 目錄一起帶走。

## 工作階段存在哪

```
~/.kohakuterrarium/sessions/<name>.kohakutr
```

`<name>` 由生物 / 生態瓶名稱加時間戳自動產生。用 `--session <path>` 覆寫，或用 `--no-session` 完全不存。

## 存了什麼

每個輪次 KohakuTerrarium 會記錄：

- **對話快照**：原始 message dict，透過 msgpack。保留 `tool_calls`、多模態內容與 metadata。
- **事件日誌**：每個 chunk、工具呼叫、子代理輸出、觸發器觸發、頻道訊息、壓縮、中斷或錯誤各一筆。這是正典的歷史。
- **子代理對話**：在子代理被銷毀前保存，事後可以檢視它做了什麼。
- **Scratchpad 與頻道訊息**：按 agent 與按頻道。
- **Job 紀錄**：長時間執行的工具與子代理的輸出。
- **可恢復的觸發器**：任何 `resumable: True` 的 `BaseTrigger` 子類別會序列化進 `state`，恢復時還原。
- **設定快照**：執行當下完整解析後的設定，所以就算磁碟上的設定改了，恢復也能重建 agent。
- **二進位產物**：產生的圖片等二進位輸出，寫在 session 檔旁邊的 `<session>.artifacts/` 下。

## 恢復

```bash
kt resume --last            # 最近的工作階段
kt resume                   # 互動選擇 (顯示最近 10 個)
kt resume my-agent_20240101 # 用名稱前綴
kt resume ~/backup/run.kohakutr
```

恢復會自動判斷類型：agent 的工作階段掛載單一生物；terrarium 的工作階段掛載完整接線，並強制 TUI 模式。

旗標與 `kt run` 相同：`--mode`、`--llm`、`--log-level`，外加 `--pwd <dir>` 覆寫工作目錄。

程式化恢復是同一套 (見[從 Python 操作工作階段](#從-python-操作工作階段))：

```python
from kohakuterrarium import Terrarium

# 從已儲存的工作階段開一個新引擎...
engine = await Terrarium.resume("runs/swe_20240101.kohakutr", pwd="/work", llm=None)

# ...或認領進一個已經在跑其他圖的引擎。
graph_id = await engine.adopt_session("runs/other.kohakutr")
```

兩者都接受路徑或 `SessionStore`；`llm=` 是選用的選擇器字串覆寫。
檔案存在但無法恢復 (未知的儲存類型、metadata 缺 config path) 會拋
`ValueError`。

恢復做的事：

1. 從 `meta` 讀設定快照。
2. 重新載入磁碟上目前的設定 (你後來改的提示詞 / 工具會生效)。
3. 合併：設定快照提供工作階段身份；目前設定提供執行邏輯。
4. 重建 agent、掛上同一個 `SessionStore`、回灌對話快照、重播 scratchpad / 頻道 / 觸發器狀態。
5. 控制器全新啟動；先前的事件在上下文裡。

也就是說小幅的設定漂移沒問題 (換 LLM、改提示詞)。結構性漂移 (改生物名稱、移除一個它正在用的工具) 可能造成重播錯誤；需要完全一致就把工作階段釘在原始設定上。

## Web UI 中的開放對話

Conversations Rail 只顯示目前仍附加 runtime 的對話。持久化對話的生命週期仍是獨立維度：

- **在線且開放：** runtime 已附加，因此對話顯示在 Rail 中。
- **休眠且開放：** 目前沒有附加 runtime。儲存的對話仍可從 Sessions 存取，但不會渲染在 Rail 中。
- **已結束：** 使用者明確結束對話。儲存的歷史仍可在 Sessions 中檢視。

關閉 Chat 或 Inspector 分頁只會 detach 該檢視，不會停止或結束對話，因此仍在線的 runtime 會保留在 Rail 中。後端將已停止或斷線的 Creature 回報為非活躍後，對應列會在下一次重新整理時消失。Sessions 歷史頁維持唯讀，並沿用既有的 **View** 與 **Resume** 操作。

每個新持久化的對話都有穩定的 `conversation_id`。runtime graph ID 在重新啟動或恢復後可以改變，檔案也可以移動或重新命名，但這些變化不會產生重複記錄。`GET /api/sessions/open` 使用此 identity 聚合在線 runtime 與帶開放 marker 的已儲存 session，並讓在線列優先；Rail 只渲染 `is_live: true` 的列。Session index 與查詢依已驗證使用者的 session directory 隔離，因此一位使用者的索引不會回傳另一位使用者的資料。

儲存的生命週期 marker 是明確的。引入 marker 之前建立的舊 session 仍可在歷史中存取。一般 runtime Stop 會保留開放 marker，並記錄 paused/dormant 狀態；明確 **End** 會清除 marker 並記錄 terminal 狀態。本機、遠端與 cluster 操作都會在 Rail 重新整理前同步儲存檔中的 marker。

Resume 依儲存對話執行 singleflight：兩個瀏覽器同時要求時只執行一次伺服器端恢復；取消其中一個等待者不會取消共享恢復，失敗後仍可重試。Cluster resume 採 all-or-error 語意：任何成員恢復失敗或儲存的連線無法重建時，伺服器會補償已恢復成員、清理 partial runtime metadata、保留原始儲存生命週期，並回傳非成功回應，而不是留下 degraded partial cluster。

## 中斷與恢復的工作流

```bash
kt run @kt-biome/creatures/swe
# 工作中... 然後在閒置時連按兩次 Ctrl+C (或 Ctrl+D / /exit)
# 之後：
kt resume --last
```

Rich CLI 模式下，Ctrl+C 會中斷進行中的輪次；閒置時連按兩次 Ctrl+C (或 Ctrl+D / `/exit`) 會優雅退出、flush session store、印出恢復提示。被強制砍掉 (SIGKILL) 會跳過最後的 flush，但因為寫入是 append-only，最近的狀態大多還是在磁碟上。

## 複製或封存工作階段

```bash
# 備份
cp ~/.kohakuterrarium/sessions/swe_20240101.kohakutr ~/backups/
cp -r ~/.kohakuterrarium/sessions/swe_20240101.artifacts ~/backups/   # 若存在

# 從移動後的位置恢復
kt resume ~/backups/swe_20240101.kohakutr
```

不恢復、只檢視，用下面的 `SessionReader`，它以唯讀模式開檔，
檢視永遠不會動到 `status` 或 `last_active`。

## 從 Python 操作工作階段

### 建立工作階段：引擎持有的持久化

持久化是引擎上的一個關鍵字參數；session metadata 由框架自己寫入並驗證：

```python
from kohakuterrarium import Terrarium

# Autosession：每張圖自動拿到 <session_dir>/<graph_id>.kohakutr
# (合併 / 分割產生的子圖也存在那裡)。
engine = Terrarium(session_dir="runs/")

# 用 session= 逐生物控制：
c = await engine.add_creature(
    "@kt-biome/creatures/general",
    session="runs/student-42.kohakutr",   # 在這個精確路徑建立 store
)
# session=True   -> 在預設 session 目錄建立
# session=False  -> 不持久化，即使 autosession 開著
# session=<SessionStore> -> 直接掛上既有的 store
# session=None (預設) -> 跟隨引擎 (autosession / 圖既有的 store / 不存)

# 配方：整張圖一個 terrarium 型的 store。
await engine.apply_recipe("@kt-biome/terrariums/swe_team", session="runs/team.kohakutr")
```

`await engine.shutdown()` (或離開 `async with` 區塊) 會關閉引擎
建立的每個 store，檔案不會再卡在 `status: "running"`。

### 恢復

- `await Terrarium.resume(store_or_path, *, pwd=None, llm=None)`：
  建一個新引擎並認領已儲存的工作階段。
- `await engine.adopt_session(store_or_path, *, pwd=None, llm=None)`：
  恢復進運行中的引擎；回傳新的 `graph_id`。

恢復從 session metadata 記錄的 config path (含 `@pkg` 參照)
重建拓樸，並以各 agent 自己的工作目錄執行，不會對你的行程
做 `os.chdir`。恢復會在建立 writer store、runtime、lifecycle 或
adoption 前執行唯讀工作目錄預檢。目錄失效時必須選擇新目錄、只看歷史或
取消，不會靜默退回 KohakuTerrarium 行程目錄。使用
`workspace_overrides={"creature-id": "/new/path"}` 只替換確認的成員；
共享失效路徑也可使用預檢回傳的 `gap_id` 成組替換。純量 `pwd=` 僅保留為
明確的全隊相容覆寫，不能和 `workspace_overrides` 同時使用。成功替換會永久
寫回 manifest；遠端與叢集會在實際 worker 上驗證路徑，並在第一次 adoption
前完成全成員預檢。叢集 API 還可依成員 session ID 限定替換，讓不同 worker
上相同的 creature 或路徑群組目標選擇不同目錄。若回滾無法持久化，session
會標記為 `partial_dirty`；後續預檢與恢復會失敗關閉，直到 session 修復。

### 讀取：`SessionReader`

`SessionReader` 是 `.kohakutr` 檔案的唯讀檢視介面。它透過
`SessionStore.open_readonly` 開啟，讀取永遠不會更新 `last_active`
或改動 `status`：

```python
from kohakuterrarium import SessionReader

with SessionReader("~/backups/swe_20240101.kohakutr") as r:
    print(r.meta["status"], r.agents)

    for turn in r.turns():               # 重組出來的 live-branch 輪次
        tools = [tc["name"] for tc in turn.tool_calls]
        print(f"[{turn.source}] {turn.user_text!r} -> "
              f"{turn.assistant_text[:60]!r} tools={tools}")

    events = r.events()                  # 原始的 append-only 日誌
    convo = r.conversation()             # 最終快照 (message dict)
    chan = r.channel_messages("tasks")   # 一條頻道的歷史

    r.index()                            # 臨時建 FTS 索引，然後：
    hits = r.search("score.json", k=5)
```

`turns()` 會跳過重新生成 / 編輯過的兄弟分支：你看到的就是
所有檢視器顯示的同一條 live branch。`search()` 只對已建索引的
工作階段回傳結果 (`kt embedding`，或用 `reader.index()` 臨時建 FTS)。

需要原始讀寫存取時，`SessionStore(path)` 還在，但任何列表 /
預覽 / 檢視器用途請用 `SessionStore.open_readonly(path)` (或直接用
`SessionReader`)：一般的開檔 + 關檔會把工作階段標成 paused 並更新
`last_active`，把「最近使用」的排序弄亂。

## 壓縮

上下文滿了之後，壓縮會縮小對話。逐生物設定：

```yaml
compact:
  enabled: true
  threshold: 0.8              # 上下文到視窗的 80% 時壓縮
  target: 0.5                 # 壓縮後目標 50%
  keep_recent_turns: 5        # 永遠原樣保留最後 N 個輪次
  compact_model: gpt-4o-mini  # 摘要這一步用便宜的模型
```

壓縮在背景執行 (見 [concepts/modules/memory-and-compaction](../concepts/modules/memory-and-compaction.md))：控制器照常跑；新摘要好了，再把對話換過去。每次壓縮都會記錄成事件。

手動壓縮：

```
/compact
```

在 CLI / TUI 提示符下。把長工作階段交接出去、或當成上下文丟給下一次執行之前，先壓一下很有用。

## 列表與搜尋工作階段

`kt serve` 的網頁 UI 與 `GET /api/sessions` 由一個 sidecar
索引支撐：`<session_dir>/.kt-index.kvault` 這一個 SQLite 檔，
快取每個工作階段列表所需的 metadata (名稱、狀態、最後活動時間、
agent、預覽…)，並對文字欄位提供 BM25 搜尋。你不會直接跟它互動；
它跨伺服器重啟與「伺服器沒開時啟動的 `kt run` 工作階段」
都保持一致。

`GET /api/sessions` 的查詢參數：

| 參數 | 預設 | 備註 |
|---|---|---|
| `limit` | `20` | 頁面大小 |
| `offset` | `0` | 頁面位移 |
| `search` | `""` | 對 `name` / `preview` / `config_path` / `agents` / `pwd` 的 FTS5 查詢 |
| `sort` | `last_active` | `last_active` \| `created_at` \| `name` \| `status` \| `relevance` |
| `order` | `desc` | `desc` \| `asc` |
| `status` | (無) | 精確比對 (`running`、`paused`…) |
| `config_type` | (無) | 精確比對 (`agent`、`terrarium`) |
| `node_id` | (無) | 精確比對，按執行該工作階段的 lab 節點過濾 |
| `refresh` | `false` | 列表前先做增量 reconcile，只重讀 `(mtime, size)` 變過的檔案 |
| `full_rescan` | `false` | 強制重讀每個檔案 (手動改過磁碟上的 `.kohakutr` 之後用) |

`sort=relevance` 只在有 `search` 時有意義；用其他排序時，
會先收集 FTS 命中集合，再按指定欄位排序。

索引怎麼在不手動 refresh 的情況下保持同步：

- **推送。** API 伺服器運行期間，它持有的每個 `SessionStore`
  以 debounce 方式把更新推進索引 (每 20 個事件或 5 秒，先到先算)，
  正常使用下索引永遠跟得上。
- **啟動時 reconcile。** 每次伺服器啟動，索引對 session 目錄做一輪
  fingerprint-diff，只重讀變過的檔案。第一次啟動會完整讀過每個檔案
  (bootstrap 步驟)，並記住已成功。
- **`?refresh=true`。** 隨需觸發同樣的增量 reconcile：
  剛把備份的 `.kohakutr` 複製進 session 目錄之後很好用。

Sidecar 可以放心刪掉：下一次列表會從 `.kohakutr` 檔重建。
索引裡沒有任何獨一無二的狀態。

不經 HTTP 層的程式化列表：

```python
from kohakuterrarium.studio.persistence.session_index import (
    get_session_index_default,
)

index = get_session_index_default()
page = index.list(search="auth bug", sort="relevance", limit=10)
for row in page.rows:
    print(row["name"], row["last_active"], row["preview"])
```

## 記憶搜尋

工作階段也是一個可搜尋的知識庫。建好索引之後：

```bash
kt embedding ~/.kohakuterrarium/sessions/swe.kohakutr
kt search swe "auth bug"
```

agent 自己可以用 `search_memory` 工具搜尋。完整走讀：[記憶](memory.md)。

## 停用持久化

有時候你只想跑一次性的：

```bash
kt run @kt-biome/creatures/swe --no-session
```

不會建立 `.kohakutr`。這也會讓壓縮無法從磁碟找回先前的輪次 (記憶體內的壓縮照常)。

## 疑難排解

- **壓縮跑不完 / OOM。** 壓縮模型跟控制器用了同一個重模型。把 `compact_model` 設成便宜的 (`gpt-4o-mini`、`claude-haiku`)。
- **恢復時報 `tool not registered`。** 生物設定變了 (某個工具被移除)，但對話還引用著它。手動編輯 `config.yaml` 把工具加回去，或開新的工作階段。
- **`kt resume` 找不到我剛剛還看到的工作階段。** 工作階段用前綴比對 `~/.kohakuterrarium/sessions/` 下的檔名。檔案改名或搬走的話，傳完整路徑。
- **複製工作階段後產生的圖片不見了。** 旁邊的 `<session>.artifacts/` 目錄也要一起複製，不只 `.kohakutr` 檔。
- **`.kohakutr` 檔很大。** 事件日誌是 append-only 的；長工作階段會長大。封存舊的，或把工作拆成多個工作階段。壓縮會縮小活躍的對話，但完整事件歷史會留著供搜尋。
- **恢復後子代理輸出不見了。** 子代理對話在子代理完成時保存。如果 parent 在子代理進行中被中斷，最新快照就是上一個 checkpoint 持久化的內容。

## 另見

- [記憶](memory.md)：對工作階段歷史的 FTS、語意與混合搜尋。
- [設定檔](configuration.md)：壓縮設定與工作階段旗標。
- [程式化使用](programmatic-usage.md)：從 Python 驅動 agent 與引擎。
- [參考 / Python API](../reference/python.md#工作階段)：`SessionReader` / `SessionStore` 簽名。
- [概念 / 記憶與壓縮](../concepts/modules/memory-and-compaction.md)：壓縮怎麼運作。
