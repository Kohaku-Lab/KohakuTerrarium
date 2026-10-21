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

工作階段 (session) 將一次執行的對話、事件、子代理對話、頻道歷史、scratchpad、job 紀錄、可恢復的觸發器與設定 metadata 記錄在 `.kohakutr` 檔案中。恢復會根據這些紀錄重建生物 (creature)，而不是為執行中的程序建立快照，也不會回復外部資源的狀態。

概念入門：[記憶與壓縮](../concepts/modules/memory-and-compaction.md)、[工作階段與環境](../concepts/modules/session-and-environment.md)。

### 壓縮後的編輯與重新產生

壓縮會改變即時提示並寫入快速恢復快照，但不會刪除追加式事件日誌。Studio 使用持久化的 `event_id`、`turn_index` 和 `branch_id` 定位可編輯的使用者訊息。儲存並重新執行或重新產生時，會從目標歷程建立新分支，只重用來源歷程仍相容的摘要與快照，不重用依賴已修改內容的表示。舊分支及其後續事件仍可切換與恢復。定位缺失、歧義、指向回合中注入輸入或與所選分支衝突時，操作會在不修改歷程的情況下失敗。

`/branch <turn_index> <branch_id>` 選擇一條血緣一致的已記錄分支，不產生新輪次。它重用相容的 checkpoint 與摘要，恢復分支座標與壓縮狀態，並儲存選擇供恢復使用。`/branch latest` 沿同一血緣選擇最新的**相容**分支，不是獨立取每個輪次的最大分支號。兩者均使用既有重建預算與訊息保留檢查；輪次、壓縮或背景工作進行期間拒絕切換。不帶參數的 `/branch` 列出候選與目前選擇。

#### 舊 checkpoint 的局部補全

相容的 checkpoint 缺少部分工具呼叫關係時，不一定要展開整段歷程。只有選定前綴與摘要已被證明可重用，重建才會保留它們，並僅補回缺失的完整 canonical assistant 輪次及其唯一歸屬的已完成結果。補全不會重新執行工具，也不會把補回的原件宣稱為已被摘要消費。舊消費紀錄不完整時，摘要與這些可追蹤的補充可能存在少量事實重複，因此不保證恢復後訊息數零增長。

以下情況會拒絕該局部補全候選：

- 來源血緣與所選分支或目標邊界衝突；
- 輪次歸屬、呼叫／job 身分或已完成結果存在歧義；
- 原始 canonical 輪次或必要結果缺失；
- 無法整輪恢復而不重複已有呼叫、不復活明確已涵蓋或已清除的內容，或不違反目標／順序邊界。

退回 raw 重建前，可以嘗試其他已證明適用的 checkpoint。一旦被拒候選帶有清除 cutoff，替代候選就必須保留相同 cutoff，不能透過 raw 回退復活已清除前綴。重建提示在安裝前仍須滿足原有訊息保留上限與設定的上下文預算（`compact.max_tokens`，不是自動壓縮觸發閾值）。准入失敗會拋出 `ConflictError`，不安裝該候選、不建立其編輯分支。token 檢查是訊息文字估算，不是 provider 精確計數。這不是一般續聊或未展開 rerun 的額外門檻，也不保證後續 provider 呼叫一定成功。

### 多模態編輯、重新產生與分支

歷程穩定時，編輯或重新產生一個使用者輪次會從事件日誌重建其原始文字、圖片與檔案內容區塊。重新產生會重用這些區塊；編輯則提供新的內容區塊清單，並建立同層分支。繼續較早的分支時，使用該分支的歷程與相容快照，恢復後也一樣。在 Studio 中只修改文字會保留附件；刪除或替換附件只影響新分支。

儲存的助理訊息與已完成工具結果保留結構化內容及呼叫／結果配對。失敗的工具也可能傳回診斷媒體或部分檔案；儲存的輸出仍保持結構化，錯誤與結束狀態另外記錄。重播已完成結果不會再次執行工具。產生圖片的位元組不同時會使用不同的產物路徑，因此新的同層分支不會覆寫較早產生的圖片。已完成的背景結果可供保留其呼叫的相容分支使用，但不適用於排除了該呼叫的分支；儲存結果本身不會繼續未選取的分支。

內嵌位元組是不可變副本：data URL 與內嵌檔案內容（包含二進位 `FilePart.data_base64`）保留在歷程中，因此修改擷取檔案在工作區中的衍生副本不會改變後續重播。二進位檔案在儲存前不會被簡化為顯示用預留文字。請求準備仍受現有檔案讀取器與 provider 能力約束；儲存位元組不代表模型能讀取所有 MIME 類型，顯示預覽仍會省略 base64 內容。即時工作區路徑（`file://` 等）仍是動態參照，準備請求時會重新從磁碟讀取。

產生的產物位於所屬工作階段檔旁的 `<session>.artifacts/`（`generated_images/`、`generated_videos/`、`tool_outputs/`）。即使 `KT_SESSION_DIR` 指向不同的預設目錄，明確指定的 `--session` 路徑、程式介面的 `session=` 或 `Terrarium(session_dir=...)` 仍決定這些產物的歸屬。送出的請求在 provider 邊界將儲存的產物 URL 解析為 data URL；對話快照保留簡短的工作階段相對參照。只解析 provider 本次選用內容中的本機圖片：被現有請求預算排除的圖片，或屬於該 provider 不會作為圖片傳送的角色的圖片，不必為本次請求保持可讀。

本版本的已知限制：

- Studio 拒絕行內編輯包含文字、圖片與檔案以外內容區塊類型的使用者訊息。
- 包含多個文字區塊的使用者訊息仍可重新執行，但 Studio 不會將這些文字改寫成單一合併欄位。
- 輪次或壓縮正在執行，或背景工作尚未完成時，實際的歷程切換、編輯／重新產生與倒回操作會被拒絕。同一分支上的一般輸入保留原有排隊行為，不會變成歷程切換。
- 快照恢復儲存的訊息與參照，而不是缺少的產物檔案、程序或外部副作用。請將工作階段檔及其產物目錄一起封存。本次必要的本機圖片缺少時，請求準備會失敗，而不是無聲省略；不保證遠端 URL 的可用性。
- 這些保證涵蓋框架現有的文字／圖片／檔案表示與所選 provider 的能力，不承諾無損視覺摘要或跨 provider 歷程遷移。

### 穩定多模態歷程與壓縮、checkpoint

編輯未壓縮的尾端時，可以重用來源輸入未變的摘要。編輯被該摘要消費過的輪次時，則會在不使用依賴該輪次的摘要的情況下重建目標前綴。返回較早分支或恢復其相容 checkpoint 時，使用該分支的有效上下文，包括保留的媒體與呼叫／結果關係。僅為恢復歷程，不會重新執行原工具或摘要程序。

壓縮向摘要模型傳送文字投影，而不是把原始圖片交給模型做視覺分析。已被所選摘要替代的媒體，不必重新出現在後續 provider 請求中，但原始事件與仍可用的產物檔案仍保留在儲存的歷程中。摘要未消費的後來工具結果，不會僅因其呼叫來自祖先輪次就一併被涵蓋；仍適用既有來源紀錄與上下文省略規則。

checkpoint 可能已包含經過省略的工具輸出，而原始歷程重建可能在重新套用目前省略策略前恢復該輸出的完整表示。這些表示不必是完全相同的字典。應在相同摘要、使用者輸入保留與省略策略下比較有效上下文；合法的完整表示恢復，不等於重新帶回已被所選摘要消費的媒體。本機媒體只針對 provider 最終有效輸入檢查可用性，因此排除的媒體不會阻斷續聊，必要媒體缺少仍明確失敗。這些規則適用於穩定歷程，不包含壓縮或背景工作仍在執行時的歷程操作。

摘要在擷取時綁定 canonical 輪次與工具結果。重用時排除保留的尾端以及被重新接上的使用者訊息，因此摘要只涵蓋它實際替代的輪次。若 canonical 歷程無法無歧義地綁定，壓縮會以 `capture_unproven` 跳過：不呼叫摘要模型，也不改動即時對話。輸入來源與結構上的替換涵蓋分開處理：經過證明的相容 completion 可以將涵蓋範圍傳遞給下一次壓縮，新摘要的來源仍保持未知，而不是變成通用的原始重播替換。局部補全的輪次也使用相同的 canonical 身分與順序規則，以便繼續壓縮。新訊息會記錄框架產生的工作狀態前綴，擷取時按生產者的相同組裝規則核對完整內容；背景工作結算後無需關閉預設工作提示即可壓縮。

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

`<name>` 由生物 / 生態瓶名稱加時間戳自動產生。`KT_SESSION_DIR` 會統一調整 CLI、Studio 與 web API 的根目錄；單次執行明確指定的 `--session <path>` 優先。產生的媒體存放在所屬執行的工作階段檔旁，包含自訂 `session=` 路徑或 `Terrarium(session_dir=...)` 指定的目錄；兩者不同時，不會改存到全域 `KT_SESSION_DIR` 下。目錄名稱固定為：provider 產生圖片使用 `generated_images/`，影片使用 `generated_videos/`，一般工具的二進位輸出仍使用 `tool_outputs/`。既有產物路徑仍可讀取且不會遷移。刪除工作階段資料不會刪除保留的產物；使用 `--no-session` 可停用工作階段持久化。

### 執行期儲存邊界

程序執行期間，請保持工作階段儲存目錄及其上層目錄的位置不變。執行時刪除、重新
命名或移動這些目錄屬於未定義行為，由呼叫端負責；透過 Web 檔案管理、Lab、編輯器、
shell 指令或外部程式進行的操作也不例外。框架不會增加檔案系統監控或權限閘門
來強制執行這項要求。

同樣，透過通用檔案 API、寫入／編輯工具或直接資料庫存取，編輯、取代、刪除或重新
命名 `.kohakutr` 資料庫及相關 SQLite 檔案（例如 `-wal`、`-shm`），不屬於
支援的執行期維護操作。正常的記錄、Memory 更新、遷移、fork 和工作階段刪除應使用
工作階段 API。與工作階段儲存無關的一般專案檔案編輯不受影響。

下文的 metadata 讀取協調僅涵蓋單機單一程序內受支援的框架操作，不協調外部程序或
繞過這些操作的任意自訂程式碼，也不重新設計既有舊版讀寫路徑之間的並行關係。

### 從原始碼 checkout 檢查離線工作階段

在相依套件可用的原始碼 checkout 中執行：

```bash
python scripts/inspect_session.py /path/to/saved.kohakutr
python scripts/inspect_session.py /path/to/saved.kohakutr --all --search "query"
```

檢查腳本擁有一個 `SessionStore.open_readonly()` 控制代碼，只關閉自己的控制代碼；
輸出失敗或中斷時也會執行關閉。它不改變工作階段狀態或最近活動時間，也不在關閉時
持久化重建的事件計數器。它沿用現有 Store 資源登記：同一程序內的快速讀取會延遲
其開啟，來源資源是否已釋放不確定時則阻止開啟。

這是**業務唯讀**，不是 SQLite `mode=ro`：native 資料表初始化和 SQLite 協調仍
可能修改 schema 或檔案位元組。請對離線工作階段使用此腳本，不要與另一個寫入同一
檔案的程序同時使用。它既不借用也不終結執行中 owner 的 Store，也不增加跨程序協調。

摘要計數列舉 key，不解碼事件／頻道／子代理的 payload；明確指定的詳細資料和搜尋
選項仍會讀取內容。開啟沒有持久化事件計數器的舊工作階段時，Store 建構階段仍可能
掃描事件值。僅按 key 統計摘要的最佳化並未消除這項獨立的舊版啟動成本。

### 已儲存 Conversation 的統計按需載入

開啟或重新整理已儲存工作階段檢視器的 **Conversation** 分頁時，會載入歷史索引、樹和
歷史頁，不主動計算統計摘要。索引中的 `viewer_agents` 按**同一個 Store** 未篩選摘要的
預設順序列出主代理和 attached 代理，不代表叢集合併摘要的完整目錄。歷史索引仍只對應
一個 Store；`targets` 保留原有合法 Conversation 目標（含頻道），發現
attached 命名空間不會自動將其變成新的 Conversation 目標。

切換至 Trace、Overview 或 Cost 時，才按需請求目前的摘要；同一資料重新整理代次重用
已成功或進行中的摘要請求。除非使用者明確選擇代理，Trace 仍等待未篩選摘要決定預設
選擇。Live Inspector 即使停留在 Conversation，也保持摘要與 header 總量更新。僅列舉
key 的代理探索不會消除舊工作階段缺少持久化計數器時的 Store 初始化掃描。

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

### Web 恢復預檢的 metadata 讀取

`POST /api/sessions/{name}/resume/preflight` 會在其**本機 metadata 讀取後段**
自動嘗試選擇性唯讀查詢，不需要設定開關或請求參數。查詢可見已提交的 SQLite WAL
資料，將 metadata 資料列讀入記憶體並釋放來源資源後，再由私有記憶體解碼器解碼。
它不會對來源檔案執行遷移、checkpoint 或業務狀態更新。

Best effort 指的是**受准入控制的最佳化**，而不是靜默復原錯誤：

- 若相關已登記的舊版資源（例如執行中的 Store 或 Memory 索引）佔用來源檔案，或
  另一個快速讀取先取得准入，本機讀取會使用既有的複製路徑。在作出此選擇前，前置
  路徑解析階段本身也可能等待正在執行的快速讀取。
- 候選來源 SQL 一旦開始，讀取或解碼失敗就會回報錯誤，不再透過複製重試。
  本機候選 SQL／解碼失敗回傳 HTTP 500，
  `detail.code = session_metadata_read_failed`。
- 若無法確認來源資源已釋放，程序內針對該檔案身分的相關已登記存取將繼續被阻止。
  預檢的資源錯誤映射回傳 HTTP 503，
  `detail.code = session_metadata_resource_unavailable`；後續請求在路徑解析
  階段遇到該狀態時也如此。這不是應盲目重試的一般忙碌回應。請停止相關工作，等待
  worker 完成，然後正常關閉／重新啟動程序；重新啟動不等於資料庫修復。來源控制
  代碼或 worker 可能仍存活時，不要手動清除佔用狀態。
- 已確認來源資源釋放後發生的私有解碼器失敗，不會隔離來源檔案。

這項最佳化不擴展到共用分類或版本探測讀取、遠端／叢集預檢分支、歷史瀏覽、其他
metadata 使用端或實際恢復操作，也不提供跨程序安全保證。上述
[執行期儲存邊界](#執行期儲存邊界)是受支援執行條件的一部分。

### CLI 恢復

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
