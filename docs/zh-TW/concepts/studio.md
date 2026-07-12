---
title: Studio
summary: Terrarium 引擎之上的管理層：目錄、身份、工作階段、持久化、attach 政策與編輯器。
tags:
  - concepts
  - studio
  - architecture
---

# Studio

## 它是什麼

**Studio** 是 `Terrarium` 執行期引擎之上的管理層。
它不是 UI，也不是另一個 agent。它是一個共用的 Python 介面，
收容那些每個 UI 和自動化腳本本來都得各自重做的事：

- 套件與內建模組的**目錄**查詢；
- LLM profile、API 金鑰、MCP、UI 偏好等**身份**狀態；
- `Terrarium` 引擎之上的活動**工作階段生命週期**；
- 已儲存工作階段的**持久化**：列表、恢復、fork、歷史、匯出；
- 即時 **attach 政策**：IO 聊天、頻道觀察者、trace、log、
  workspace 檔案、pty；
- Studio **編輯器**：workspace 的生物 / 模組 CRUD 與 scaffolding。

Python 門面是 `kohakuterrarium.Studio`。HTTP API、網頁 UI、
`kt` 指令和你自己的程式碼都該委派給同一套 Studio 操作，
而不是各自複製目錄 / 工作階段 / 設定的邏輯。

## 層級堆疊

用三個程式化門面來想：

| 門面 | 層級 | 擁有什麼 |
|---|---|---|
| `Agent` / 生物內部 | 生物 (creature) | 一個 LLM 控制器，帶工具、觸發器、子代理、外掛、記憶、I/O。 |
| `Terrarium` | 執行期引擎 | 活著的生物、圖拓樸、頻道、輸出接線、熱插拔、引擎事件。 |
| `Studio` | 管理層 | 目錄、身份、活動工作階段、已儲存的工作階段、attach 政策、編輯器流程。 |

下層不 import 上層：

- 生物的程式碼不知道 `Terrarium` 或 `Studio` 存在。
- `Terrarium` 托管生物，但不知道 `Studio`、HTTP 或 CLI。
- `Studio` 包住一個 `Terrarium` 引擎 (傳 `engine=`，或讓
  `Studio()` 自己持有一個)，在上面疊管理語意。它的狀態以實例
  為範圍：兩個 Studio 包兩個引擎，session 註冊表永遠不共享。
- `api/`、`cli/` 和前端都是 Studio 之上的 adapter。Studio
  本身拋出型別化的 `kohakuterrarium.errors` 例外；只有
  `api/` adapter 會把它們轉成 HTTP 狀態碼。

整體結構是：一個執行期引擎、一個管理層，加上薄薄的 UI adapter。

## 為什麼需要 Studio

在 Studio 之前，同樣的責任散落在好幾個地方：

- 套件列表同時在 `kt list` 和網頁路由裡各寫一份；
- profile / 金鑰 / MCP 的邏輯分散在 `kt config`、`kt model`、
  `kt login` 和 `/api/settings`；
- 活動 agent 與 terrarium 路由各自重複一套生命週期邏輯；
- 已儲存工作階段的檢視 / 匯出 / diff / 恢復程式碼跟執行期的
  session 建立分家；
- WebSocket 的聊天 / log / 檔案 / 終端端點各自有自己的 attach 政策。

Studio 把這些變成每個關注點只有一份實作。CLI 印出終端形狀的
輸出，HTTP API 序列化 JSON，前端渲染面板，但工作都是請
Studio 做的。

## Studio 的工作階段 vs Terrarium 的圖

`Terrarium` 擁有**圖**：活著生物的連通元件。
獨立生物是一張圖。多生物團隊也是一張圖。
連接兩張圖會合併；斷開可能造成分割。

當使用者或 UI 在管理一張圖時，Studio 把它叫做**工作階段
(session)**。這個工作階段 handle 帶著：

- `session_id`：圖的 id；
- `kind`：單生物圖是 `"creature"`，從配方啟動的多生物圖是
  `"terrarium"`；
- 生物摘要，給 UI 分頁和生物層級操作用；
- Studio 在意的 metadata，例如 config path、工作目錄、建立時間。

這也是公開的活動工作階段 API 用
`/api/sessions/{sid}/creatures/{cid}/...` 這種 URL 的原因：
生物操作永遠以擁有它的圖 / 工作階段為範圍。

已儲存的工作階段是另一回事：它們是磁碟上的 `.kohakutr` 檔。
Studio 的 persistence 可以列出它們、恢復進運行中的引擎、
fork 它們，並產生事後檢視用的 payload。

## 被托管的設定歸屬

有些執行期能力由 operator 設定，而不是由 recipe 或生物設定。
[Drive 執行期](multi-agent/drive.md)是當前的例子，它演示了歸屬規則。

引擎是 **相依注入的**：`Terrarium` 接受顯式的 `drive_config` /
`drive_registrations` / `drive_store` 參數，從不讀 `~/.kohakuterrarium`
或向 Studio 要什麼。**Studio 是被托管的設定歸屬方**：它載入並校驗設定
檔案（[`drive-settings.yaml`](../reference/configuration.md)），把它和
已安裝註冊項目錄聯合起來，並解析出一份顯式的 spec，由 Studio 支援的
建構路徑注入到它建構的引擎裡。

```text
web / CLI / TUI 轉接器
  -> Studio 設定 + 目錄
  -> 解析顯式 Drive 參數
  -> Terrarium(drive_config=..., drive_registrations=...)
  -> creature 注入
```

由這個方向而來的後果：

- 一個程式化呼叫者可以完全繞過 Studio 並傳顯式物件（見
  [Programmatic Drive](../guides/programmatic-drive.md)）。把一個既有引擎
  傳給 `Studio(engine=...)` 絕不會用設定檔覆寫那個引擎的顯式設定。
- 儲存設定和把它們套用到一個活引擎是 **分離的** 型別化操作，所以 UI
  真實地報告 `applied_live` / `restart_required` / `rejected`，而不是
  假裝一個已儲存的檔案正在執行。
- HTTP 路由和 web 設定面板委託給同一個 Studio 門面；它們不是第二個設定
  歸屬方。

## Attach 政策

不是每隻生物都是聊天機器人。監控生物可能沒有使用者輸入；
排程生物可能只發 log；多代理團隊可能需要的是頻道觀察者
而不是聊天框。Studio 把**運行**一隻生物跟把 UI **attach**
上去分開。

Attach 政策回答的是：「對這隻運行中的生物或工作階段，
什麼即時視圖 / 控制介面才合理？」

| 政策 | 形狀 | 用途 |
|---|---|---|
| IO 聊天 | 讀寫串流 | 對話型生物。 |
| 頻道觀察者 | 唯讀串流 | 檢視圖的頻道流量，不打擾監聽者。 |
| Trace | 唯讀串流 | 引擎事件、輪次、拓樸變化、工具活動。 |
| Log | 唯讀串流 | 行程 / 執行期 log。 |
| Workspace 檔案 | 瀏覽 / 監看 | 檔案面板與編輯器刷新。 |
| PTY | 讀寫終端 | 接到生物工作目錄的 shell。 |

網頁 dashboard 透過 HTTP / WebSocket adapter 暴露這些。
`Studio.attach` 命名空間目前負責宣告可用的政策；
更多程式化的串流輔助方法可以加在那裡，不需要改動執行期引擎。

## 別把 Studio 跟網頁 dashboard 搞混

網頁 dashboard 是一個 UI。Studio 是 dashboard 呼叫的 Python
管理層。不開網頁伺服器也能用 Studio：

```python
from kohakuterrarium import Studio

async with Studio() as studio:
    session = await studio.sessions.start_creature("@kt-biome/creatures/general")
    print(session.session_id)
```

你也可以跑網頁 dashboard，它把 FastAPI 路由與 WebSocket 端點
掛在同一套 Studio / Terrarium 概念上：

```bash
kt web
```

兩條路共享同一個心智模型：Studio 管理工作階段；
Terrarium 運行生物。

## 什麼時候用哪一層

- 需要對單一生物的模組、事件佇列、輸出 handler 或測試 harness
  做完整低階控制時，直接用 **`Agent`**。
- 需要執行期拓樸時用 **`Terrarium`**：加生物、接頻道、熱插拔、
  觀察引擎事件。
- 在做 UI、服務、自動化或腳本，需要面向使用者的管理功能（套件、
  設定、活動工作階段、已儲存的工作階段、attach 政策、
  編輯器）時，用 **`Studio`**。

## 另見

- [Terrarium](multi-agent/terrarium.md)：Studio 包住的執行期引擎。
- [程式化使用](../guides/programmatic-usage.md)：怎麼嵌入 `Studio` 與 `Terrarium`。
- [Studio 使用指南](../guides/studio.md)：任務導向的範例。
- [Python API](../reference/python.md)：簽名與命名空間地圖。
