---
title: MCP 伺服器
summary: 在一個統一的 MCP 端點上註冊多個工作區，連接外部用戶端，管理工具任務，並視需要啟用本機 Creature 或子代理委派。
tags:
  - guides
  - mcp
  - deployment
---

# MCP 伺服器

MCP 伺服器讓外部 MCP 用戶端使用本機上已註冊工作區中的 KT 能力。用戶端可以直接呼叫檔案與命令工具，也可以視需要把任務委派給本機 Creature 或一次性子代理。只使用直接工具時，不會建立 Creature，也不會啟動本機模型。

`kt mcp-serve` 在每個 KT 設定環境中管理一個**統一端點**：一個背景程序、一個具備驗證機制的 Streamable HTTP 端點，以及選用的 ngrok 通道。你在這個端點上註冊一個或多個工作區，用戶端透過 `workspace_id` 選擇要操作的目錄。本指南介紹的是「讓外部用戶端呼叫 KT」；若要讓 Creature 呼叫其他 MCP 伺服器，請閱讀 [MCP 用戶端設定](mcp.md)。

> **開放前請確認權限範圍。** 預設工具包含檔案寫入與命令執行；工作區是預設執行目錄，**不是沙箱**。完整連線 URL 含存取密鑰，持有它即可存取該端點上**所有已註冊的工作區**，應當視為憑證保管。詳細說明見 [存取與隔離邊界](#存取與隔離邊界)。

首次使用請從 [首次連線設定](#首次連線設定) 開始。已經連通時，可直接查看 [註冊與管理工作區](#註冊與管理工作區)、[呼叫工具與管理任務](#呼叫工具與管理任務)、[自訂直接工具與外掛](#自訂直接工具與外掛) 或 [委派給本機 Creature 或子代理](#委派給本機-creature-或子代理)。從舊版「每個工作區一個端點」升級時，見 [從舊版依工作區端點遷移](#從舊版依工作區端點遷移)；連線或執行異常請見 [疑難排解](#疑難排解)。

## 首次連線設定

### 準備條件與連線模式

安裝包含 `kt mcp-serve` 的 KT 版本，並準備一個穩定的公開 HTTPS 入口。兩種連線模式擇一使用：

| 模式 | 你需要準備 | KT 負責的部分 |
| --- | --- | --- |
| `ngrok` | 已安裝並完成驗證設定的 ngrok、帳號與固定 HTTPS 網域 | 啟動、監督與回收 ngrok 通道程序 |
| `external` | 自行維護的穩定 HTTPS 入口，並將請求轉送至本機回送連接埠 | 只管理本機 MCP 服務，不啟動或停止外部通道 |

本機連接埠預設為 8765。使用 `external` 時，將入口轉送至 `http://127.0.0.1:8765`；選擇其他連接埠時相應調整。即使主機本身可公開存取，也需要 HTTPS 反向代理：KT 只監聽回送位址，不自行終止 TLS。入口部署可參考 [反向代理部署](deployment-reverse-proxy.md)。

精靈中的**公開 HTTPS origin** 是入口的協定、網域及選用連接埠，例如 `https://your-domain.example`，不含 MCP 路徑。它不是稍後要填入用戶端的完整連線 URL。

### 設定、註冊工作區並啟動

以下命令可以在任意目錄執行，它們管理的是目前 KT 設定環境中的端點（見 [選擇設定環境](#選擇設定環境)）：

```powershell
kt mcp-serve setup
kt mcp-serve workspace add myproject ./my-project
kt mcp-serve start
kt mcp-serve url
```

`setup` 開啟互動式設定精靈，依序選擇連線模式、公開 origin、本機連接埠、選用的 MCP 工具設定檔，以及託管模式下的 ngrok 執行檔與設定檔。首次只使用預設工具時，可以不指定工具設定檔。儲存前會顯示變更摘要並要求確認；在精靈中取消或輸入結束（EOF）不會改動原設定。

**`setup` 只儲存端點設定。** 它不會註冊工作區、啟動服務、安裝 ngrok、註冊帳號、分配網域或測試公開連通性。儲存前會檢查本機相依項目；各項檢查的時機見 [命令參數與非互動使用](#命令參數與非互動使用)。

`workspace add` 以 `myproject` 為名稱註冊一個已存在的目錄，用戶端之後用這個名稱作為 `workspace_id`。可以註冊多個工作區；詳細規則見 [註冊與管理工作區](#註冊與管理工作區)。沒有註冊任何工作區時，端點也能啟動，但用戶端除了 `workspaces` 以外沒有可操作的目錄。

`start` 使用已儲存的設定啟動服務，並等待公開就緒檢查。結束碼 0 表示已透過公開端點完成具驗證的初始化，並確認連到目前實例；非零結束碼不一定表示本機程序未啟動，見 [區分本機就緒與公開就緒](#區分本機就緒與公開就緒)。

### 新增至用戶端

服務就緒後，供人閱讀的啟動輸出會顯示完整連線 URL，也可透過 `kt mcp-serve url` 再次取得。在支援 Streamable HTTP 的外部用戶端中新增 MCP 伺服器，貼上這個**完整 URL**，不要只填入公開 origin。

完整 URL 的形式為 `https://your-domain.example/mcp/<密鑰>`，其中密鑰是首次執行 `setup` 時產生的 43 個字元的隨機字串。一個設定環境只有一個 URL，它涵蓋該端點上註冊的所有工作區。

完整 URL 含存取密鑰，不要放入版本控制、一般日誌或公開截圖。用戶端要求的工具呼叫確認仍由用戶端處理，KT 不會繞過確認。

### 驗證首次工具呼叫

連線後，先讓用戶端呼叫 `workspaces`，確認回傳的清單中包含你註冊的名稱以及 `instance_id`；這一步不會載入任何工作區。再呼叫 `tree(workspace_id="myproject")` 查看工作區根目錄，驗證一次唯讀工具操作。這一步會載入該工作區的執行環境，但不需要啟動本機模型，也不需要寫入檔案。

到這裡應分別確認四個結果：端點設定已儲存、工作區已註冊、伺服器公開就緒、用戶端實際能呼叫工具。只看到 `setup` 成功，並不代表後面幾步已經完成。

## 日常啟停與狀態

### 啟動、停止與重新取得 URL

設定完成後，日常使用不需要重複執行 `setup`：

```powershell
kt mcp-serve start
kt mcp-serve status
kt mcp-serve url
kt mcp-serve stop
```

重複或同時執行 `start` 會重用現有實例，不會重複啟動，也不會自動套用[待生效設定](#目前設定與待生效設定)。`stop` 會取消所有工作區中的所屬任務、關閉本機監聽並回收所管理的 ngrok 程序，保留端點設定與工作區註冊，不刪除雲端資源。再次啟動會重用這些設定，但會建立新的執行實例；任務與工作階段的生命週期見 [呼叫工具與管理任務](#呼叫工具與管理任務)。

監督程序不是開機服務。當機或重新啟動電腦後，需要重新執行 `start`。

### 選擇設定環境

端點依 **KT 設定環境** 劃分，而不是依目前目錄劃分。預設設定環境是環境變數 `KT_CONFIG_DIR` 指定的目錄；未設定時為 `~/.kohakuterrarium`。所有子命令都可以用 `--home-dir PATH` 指定其他設定環境，例如：

```powershell
kt mcp-serve status --home-dir ./another-kt-home
```

每個設定環境有獨立的端點設定、密鑰、工作區註冊表與背景程序，相關檔案儲存在 `<設定環境>/mcp-serve/endpoint/` 下。需要兩個互不相通的端點（例如使用不同密鑰開放給不同用戶端）時，使用兩個設定環境，並為它們設定不同的本機連接埠。

端點設定損毀時，`setup`、`start`、`status`、`url` 與 `rotate` 都會拒絕繼續操作；處理方法見 [密鑰外洩或端點紀錄損毀](#密鑰外洩或端點紀錄損毀)。

### 區分本機就緒與公開就緒

`start` 預設最多等待 30 秒，可透過 `--wait` 指定 1–120 秒的等待時間。例如：

```powershell
kt mcp-serve start --wait 60
kt mcp-serve status --json
```

只有狀態為 `ready` 且公開檢查通過時，`start` 才回傳 0。結束碼 1 可能表示本機服務已經執行，但公開端點尚未連通或管理通道異常。此時查看狀態中的 `state`、`local_ready`、`public_ready`、`tunnel_state` 與最近公開檢查時間，不要只憑啟動命令的結束碼判斷程序是否存在。公開就緒狀態始終針對正在執行的實例，而不是尚未生效的設定。

`status --json` 中與就緒相關的欄位：

| 欄位 | 取值與含義 |
| --- | --- |
| `state` | `starting` 啟動中；`ready` 本機已監聽，且最近一次公開檢查通過；`offline` 本機已執行，但公開檢查未通過或通道未執行；`degraded` 服務在執行，但本機管理通道異常，見 `management`；`failed` 啟動或執行出錯，原因見 `error`；`stopped` 未執行；`unresponsive` 程序仍持有實例鎖，但執行狀態超過 30 秒未更新或無法讀取 |
| `local_ready` / `public_ready` | 本機監聽是否就緒 / 最近一次公開檢查是否通過 |
| `tunnel_state` | `ngrok` 模式下為 `starting`、`connecting`、`online`、`reconnecting` 或 `stopped`；`external` 模式下固定為 `external` |
| `public_checked_at` | 最近一次公開檢查的 Unix 時間戳記（秒）。檢查通過後約每 10 秒複查一次，未通過時約每 2 秒重試 |
| `management` | 本機管理通道的狀態，用於執行中的 `workspace` 命令；`state` 為 `ready`、`busy`、`degraded`、`failed`、`stopping` 或 `stopped` |
| `error` | 最近一次錯誤訊息，不含密鑰 |
| `home_dir` / `record_path` | 所屬設定環境 / 端點紀錄 `connection.json` 的完整路徑 |

如果等待到期時監督程序尚未接管實例，啟動命令會回收該子程序並回報錯誤。較慢的主機可增加 `--wait` 後重試。

## 註冊與管理工作區

### 註冊、查看與移除

工作區只能在本機透過 CLI 註冊與移除，用戶端無法遠端修改註冊表：

```powershell
kt mcp-serve workspace add myproject ./my-project
kt mcp-serve workspace list
kt mcp-serve workspace remove myproject
```

`workspace add NAME PATH` 註冊一個已存在的目錄，相對路徑依命令執行時的目前目錄解析，並解析符號連結與 Windows 路徑大小寫。名稱必須以字母或數字開頭，只能包含字母、數字、`_`、`.` 與 `-`，最長 64 個字元，並且在同一設定環境中唯一。名稱與目錄無關：移動目錄不會自動更新註冊，同一目錄也可以用不同名稱重複註冊，每個名稱各自擁有獨立的執行環境。

這三條命令的輸出一律是 JSON；加上 `--json` 時輸出為單行。

### 工作區執行環境與狀態

每個註冊的工作區在第一次被工具呼叫時才載入自己的執行環境，並在之後一直保留，直到服務停止或該工作區被移除。用戶端呼叫 `workspaces` 可以看到每個工作區的 `state`：

| 狀態 | 含義 |
| --- | --- |
| `unloaded` | 尚未載入；第一次工具呼叫時載入 |
| `ready` | 執行環境已載入，可以處理呼叫 |
| `unavailable` | 上次載入失敗，原因見 `error`，例如目錄已被刪除；下一次呼叫會重新嘗試載入 |
| `draining` | 正在移除，不再接受新呼叫 |

每個工作區的執行環境互相獨立，各自擁有任務清單、檔案讀取紀錄、`pwd_guard` 放行紀錄與委派工作階段。工具結果中的 `instance_id` 是該工作區執行環境的識別碼，`server_instance_id` 是整個端點實例的識別碼。

### 服務執行時的管理命令

服務停止時，`workspace` 命令直接修改註冊表。服務執行時，`add`、`remove` 與 `list` 會透過私密的本機檔案交給正在執行的實例處理，結果立即生效：新註冊的工作區馬上出現在 `workspaces` 中，`list` 顯示即時狀態。這類命令逐條依序處理，並與 `start`、`stop` 等生命週期命令互斥。

對執行中的端點執行 `remove` 時，如果該工作區還有執行中的呼叫、未完成的任務或未關閉的委派工作階段，命令會拒絕執行，並提示先完成任務、關閉工作階段，或使用 `--force`。`--force` 會取消這些呼叫並關閉執行環境，然後刪除註冊。移除後，該工作區的所有 `job_id` 與 `session_id` 都失效。

如果命令回報結果**未確認**（`Workspace command outcome is unconfirmed`），表示命令可能已經執行，也可能沒有。此時先執行 `workspace list` 查看實際狀態，再決定是否重試，不要直接重複執行。管理通道持續異常時，`status` 中的 `state` 會顯示為 `degraded`，需要停止並重新啟動端點；這不會影響已在執行的工具呼叫與 HTTP 服務。

## 呼叫工具與管理任務

### 預設工具與一般呼叫

用戶端先呼叫 `workspaces` 取得已註冊的名稱。除 `workspaces` 外，每個工具都必須帶上 `workspace_id` 參數；缺少或名稱未知時，呼叫會回傳錯誤並提示先呼叫 `workspaces`。

不指定工具設定檔時，每個工作區提供九個直接工具：`read`、`write`、`edit`、`multi_edit`、`glob`、`grep`、`tree`、`bash` 與 `python`，工作區目錄是它們的預設工作目錄。

前景呼叫回傳 KT Executor 的 `job_id`、輸出、錯誤、結束碼與中繼資料，並附帶 `workspace_id`、`instance_id` 與 `server_instance_id`。`job_id` 識別該工作區中的一次執行，後續查詢、等待或取消都使用該 ID 與同一個 `workspace_id`，不需要重新執行原操作。

讀取圖片檔案時，結果以 MCP 圖片內容回傳。讀取 PDF 時回傳每頁文字；算繪後的頁面圖片目前不會以 MCP 圖片內容回傳。

### 背景執行、查詢與等待

對 `bash` / `python` 傳入 `run_in_background: true` 會立即回傳**同一個 KT job ID**，執行繼續進行。以下四個任務工具用於查詢與控制這些任務：

| 工具 | 行為 |
| --- | --- |
| `job_status` | 讀取單個任務；省略 `job_id` 時列出該工作區保留的任務及 `instance_id` |
| `job_wait` | 等待 0–60 秒，預設 10 秒；逾時回傳目前狀態 |
| `job_cancel` | 取消所屬的執行中任務；Creature 委派的取消範圍更大，見 [補充輸入與取消](#補充輸入與取消) |
| `job_promote` | 將前景呼叫釋放至背景，保留原 job ID，不重複執行 |

任務不會僅因經過一段時間就自動轉背景。等待逾時、等待請求中斷連線或被取消，都不會取消所屬任務；需要停止執行時，應明確使用 `job_cancel`。

**背景完成不會自動喚醒 ChatGPT 對話。** 用戶端需要主動查詢或等待結果。本機委派也使用這些任務工具，但委派提交本身已經非同步，無須再呼叫 `job_promote`。

### 取消、重試與結果保留

任務結果與 MCP 查詢是否成功是兩件事。即使任務失敗或被取消，讀取或等待其保留紀錄仍是成功的 MCP 呼叫；`state`、`error` 與 `exit_code` 描述的是任務結果。未知任務、無效查詢參數與前景執行失敗仍屬於 MCP 錯誤。

`job_id` 與 `session_id` 只屬於建立它們的工作區執行環境。未知或已過期的 ID **不要換一個 `workspace_id` 重試**；變更操作的 HTTP 回覆遺失後，也不要立即重複提交。先用同一個 `workspace_id` 呼叫 `job_status` 查詢；涉及委派時，同時檢查工作階段狀態，避免把回覆遺失誤當成任務未執行。

每個工作區執行環境最多保留 100 個已完成任務，直接工具任務與委派任務採用相同的有界保留規則。正常停止伺服器會取消所屬任務；重新啟動會建立新實例，移除工作區會關閉其執行環境，兩者都不會還原舊任務，舊 ID 不會匹配新任務。連線 URL 的身分與這些執行狀態互相獨立，URL 不變不代表舊任務仍然存在。

## 自訂直接工具與外掛

### 設定檔與路徑規則

設定分為幾個不同層次，不要把它們當成同一種檔案：

| 設定 | 負責什麼 | 如何指定 |
| --- | --- | --- |
| 端點設定 | 密鑰、公開 origin、連接埠、連線模式及外部設定檔路徑 | 由 `kt mcp-serve setup` 管理 |
| 工作區註冊表 | 工作區名稱與目錄的對應關係 | 由 `kt mcp-serve workspace` 管理 |
| MCP 工具設定檔 | 直接工具、執行外掛與委派目標註冊，對所有工作區生效 | `setup --config ./mcp.yaml` |
| Creature 或子代理定義 | 委派目標自己的模型、工具、外掛與執行限制 | MCP 工具設定中的 `delegation.<別名>.config` |
| ngrok 設定檔 | ngrok 本身的設定 | 託管模式下的 `setup --ngrok-config PATH` |

端點設定儲存在 `<設定環境>/mcp-serve/endpoint/connection.json`，工作區註冊表儲存在同目錄的 `workspaces.json`，後續啟動都會重用。自訂直接工具時，另建獨立的 YAML 或 JSON 檔案，而不是把一般 Creature 設定直接交給 `--config`。

**MCP 工具設定檔是全域的，不能包含 `workspace` 欄位**，否則會被拒絕；它定義的工具、外掛與委派目標會套用到每個註冊的工作區。檔案中執行外掛的 `module` 與委派目標的 `config` 若為相對路徑，以**設定檔所在目錄**為基準解析；`@package/...` 引用沿用 KT 套件解析規則。

### 選擇工具與設定執行參數

範例 `mcp.yaml`：

```yaml
name: KT tools
pwd_guard: warn
tools:
  - name: read
  - name: write
  - name: edit
  - name: multi_edit
  - name: glob
  - name: grep
  - name: tree
  - name: bash
    config:
      timeout: 60
      max_output: 262144
  - name: python
    config:
      timeout: 60
plugins: []
```

儲存設定路徑，再重新啟動服務以啟用它：

```powershell
kt mcp-serve setup --config ./mcp.yaml
kt mcp-serve stop
kt mcp-serve start
```

省略 `tools` 會啟用前述九個工具。工具名稱必須唯一，`type` 必須為 `builtin`（預設值）。支援 `max_output` 及各工具宣告的執行時選項：`timeout` 適用於 bash/Python，`env` 適用於 bash。不接受逐工具 `working_dir`，因為目錄由各工作區的執行上下文提供。

MCP 工具設定頂層不接受 `workspace`、控制器通知設定、LLM 設定、提示詞、觸發器、compact 或 AgentConfig 繼承；這些欄位會被明確拒絕，而不是悄悄忽略。需要本機模型執行任務時，應使用下一節的委派目標設定。

工作區目錄是預設執行位置，**不是沙箱**。KT 原有的先讀後寫、過期讀取檢查、路徑保護與執行策略仍然適用。`pwd_guard` 控制對工作區外路徑的存取。預設的 `warn` 會攔截首次存取某個工作區外路徑的操作並回傳警告，對同一路徑再次執行即放行；`block` 一律拒絕；`off` 不檢查。放行紀錄只在該工作區的目前執行環境內有效，並由存取這個工作區的所有用戶端共享；存取另一個工作區時需要重新放行。

### 執行外掛及其能力限制

直接工具的執行外掛使用一般 `name`、`type`、`module`、`class` 與 `options` 設定，只支援執行側能力：載入與卸載、分派、執行前後鉤子、執行時服務與轉背景。外掛在端點啟動時統一檢查，載入失敗會中止啟動；之後每個工作區載入執行環境時還會各自實例化外掛，此時失敗會使該工作區進入 `unavailable` 狀態。覆寫 LLM、Agent 生命週期、事件、compact、提示詞、可見性、命令或終止鉤子的外掛會被拒絕。

這些外掛在執行時能取得的上下文只有工作目錄、名稱與實例 ID，沒有宿主 Agent、Controller、工作階段持久化、模型切換或子代理建立能力。自訂外掛需要遵守此約定；外掛屬於受信任的本機程式碼。這裡的限制針對直接工具執行環境，不取代委派目標自己的外掛設定。

## 委派給本機 Creature 或子代理

本節是選用能力。只使用直接工具時，無須準備模型設定或註冊委派目標。

> 委派目標共享工作區檔案，但使用自己的工具與外掛策略。直接 MCP 工具允許清單不會限制委派能力；啟用前請核對目標定義中的權限與執行限制，詳見 [存取與隔離邊界](#存取與隔離邊界)。

### 選擇目標類型

| 類型 | 適用方式 | 對話生命週期 |
| --- | --- | --- |
| `creature` | 需要多輪延續，或使用 Creature 的工具、外掛與自動觸發器 | 可用 `session_id` 延續；已關閉的工作階段不能延續 |
| `subagent` | 一次性任務，無須父 Creature | 執行中可補充輸入，完成後不能在同一對話繼續；新任務會建立新子代理 |

目標只從本機設定中註冊，並對每個工作區都可用。用戶端可以選擇別名，但不能提交設定路徑、內嵌定義，或覆寫模型與工具設定。僅註冊目標不會立即建立實例或啟動模型。

### 註冊目標並準備模型設定

先用一般 KT 命令設定本機模型憑證與模型設定，並準備好目標定義。Creature 使用一般 KT 設定格式；下面的 `coder` 範例要求已安裝包含該定義的 `@kt-biome` 套件。

在前述 `mcp.yaml` 中新增 `delegation` 欄位。下面只展示委派部分；既有的 `tools`、`plugins` 等欄位可以保留：

```yaml
delegation:
  coder:
    kind: creature
    config: "@kt-biome/creatures/swe"
    description: "在指定工作區實作並驗證修改"
  reviewer:
    kind: subagent
    config: ./reviewer.yaml
    description: "審查具體修改並回報發現"
```

目標定義的相對路徑以 MCP 工具設定檔所在目錄為基準。定義在委派實例建立時載入；錯誤定義會使該任務失敗，不會悄悄捨棄已設定的能力。

獨立子代理的 YAML/JSON 檔案使用一般子代理定義的欄位（見 [子代理](sub-agents.md)）。將以下內容儲存為與 `mcp.yaml` 同目錄的 `reviewer.yaml`；`llm: default` 引用已設定的本機 KT 模型設定：

```yaml
name: reviewer
llm: default
system_prompt: "審查請求中的修改，回報具體發現。"
tools:
  - name: read
  - name: glob
  - name: grep
  - name: bash
    config:
      timeout: 60
can_modify: false
max_turns: 30
timeout: 600
plugins: []
```

子代理的 `tools` 支援工具名稱或一般工具設定項目。自訂工具、套件工具與外掛的寫法與一般 KT 設定相同，相對路徑以定義檔所在目錄為基準。省略 `llm` 時，也可透過 `model` 選擇子代理模型；這裡沒有父模型可繼承。目前不支援互動式子代理。執行限制與沙箱策略應設定在目標定義及其外掛中。

若尚未儲存 `mcp.yaml` 的路徑，請執行 `kt mcp-serve setup --config ./mcp.yaml`。接著執行 `kt mcp-serve stop`、`kt mcp-serve start`，並重新整理用戶端工具清單。修改目標清單需要重新啟動伺服器；修改所引用的定義只影響新建立的委派實例，不改變現有實例。

### 發起任務與延續工作階段

註冊委派目標後，會增加六個工具，它們同樣需要 `workspace_id`：

| 工具 | 用途 |
| --- | --- |
| `delegation_targets` | 列出目標別名與描述，不啟動模型 |
| `delegate` | 提交 `target`、`prompt`，可用 `session_id` 延續 Creature 工作階段 |
| `delegation_send` | 向執行中的委派任務（依 `job_id`）補充資訊 |
| `delegation_sessions` | 列出該工作區中本伺服器擁有的工作階段、忙碌狀態與目前委派任務 |
| `delegation_history` | 分頁讀取工作階段活動或目前公開對話快照 |
| `delegation_close` | 停止並關閉本伺服器擁有的工作階段，保留可讀取的歷史 |

委派實例以所選工作區為工作目錄，工作階段屬於該工作區的執行環境，只能用同一個 `workspace_id` 存取。一次典型的 Creature 委派流程如下。以下是 MCP 工具呼叫示意，不是終端命令：

1. 呼叫 `delegation_targets(workspace_id="myproject")`，選擇目標別名。
2. 呼叫 `delegate(workspace_id="myproject", target="coder", prompt="調查失敗的測試")`，儲存回傳的 `job_id` 與 `session_id`。前者識別本次執行，後者識別對話；提交會在模型執行前回傳。
3. 使用 `job_status` / `job_wait` 取得結果，或用 `delegation_history` 查看活動。等待與重試遵循前述 [任務管理規則](#呼叫工具與管理任務)，無須 `job_promote`。
4. 本回合結束後，用 `delegate(workspace_id="myproject", target="coder", session_id=..., prompt="套用修正")` 繼續對話。省略 `session_id` 會建立獨立對話；不再需要工作階段時呼叫 `delegation_close`。

每個 Creature 工作階段同時接受一個活動委派回合。忙碌的工作階段會拒絕新任務，而不是將它排隊。自動觸發回合也可能使工作階段忙碌，此時不一定存在 MCP 委派 job ID。

`delegation_sessions` 讀取 Creature 的即時狀態，包括 `idle`、`paused` 與 `stopped`，不根據目標設定推斷狀態。遇到忙碌的工作階段時，可結合歷史查看它正在做什麼。

### 補充輸入與取消

執行期間，用 `delegation_send` 向活動中的 `job_id` 補充資訊。補充輸入不是另一項排隊委派。對 Creature 而言，補充輸入會先被緩衝，在目前這批工具呼叫的結果寫入對話之後、下一次呼叫模型之前併入本回合對話。只有委派任務仍在執行時才會接受補充輸入；回傳結果中 `accepted` 為 `false` 表示沒有送達，例如任務已結束或正在取消。處理補充輸入時，KT 可能將前景工具轉為背景，隨後原委派回合結束。

**Creature 回合結束不等於其背景任務全部結束。** 自動活動屬於工作階段歷史，不會被當作無關任務的結果。需要停止執行時，依希望保留的工作階段狀態選擇操作：

| 操作 | 停止範圍 | 之後是否可以延續 |
| --- | --- | --- |
| 對執行中的 Creature 委派執行 `job_cancel` | 重用 KT stop：停止該 Creature、觸發器及所有由 KT 管理的工具與子代理，包括先前回合留下的背景工作；等待清理並保留歷史 | 在同一伺服器生命週期內，可明確使用原 `session_id` 延續；取消本身不會自動重新啟動 |
| 對執行中的獨立子代理委派執行 `job_cancel` | 只停止該子代理自己的任務範圍，並等待原有取消鏈完成 | 一次性子代理不能延續，後續任務會建立新實例 |
| 執行 `delegation_close` | 停止並關閉指定工作階段，保留可讀取的歷史 | 已關閉的工作階段不能延續 |
| 移除工作區或停止 MCP 伺服器 | 取消所屬任務，結束相關工作階段的生命週期 | 不會自動還原任務或工作階段，舊 MCP 控制代碼不再有效 |

> 對**已完成任務**呼叫 `job_cancel` 不會產生效果。要停止 Creature 工作階段中剩餘的背景工作，應使用 `delegation_close`。取消不會回復檔案修改，也不承諾回收任意脫離 KT 管理的作業系統程序。

取消 Creature 後，明確延續同一個、本伺服器擁有的工作階段，會從已持久化的工作階段檔案重建執行環境（見 [查看歷史與工作階段生命週期](#查看歷史與工作階段生命週期)）。這與「關閉工作階段」或「重新啟動伺服器」不同，不應混為一談。

### 查看歷史與工作階段生命週期

`delegation_history(workspace_id=..., session_id=..., view="events")` 查看活動；`view="conversation"` 查看公開訊息與完整保留的工具結果。兩種檢視透過 `cursor` 與 `limit`（1–200）分頁，但保留與分頁方式不同：

| 檢視 | 需要注意的限制 |
| --- | --- |
| `events` | 只保留最新 2,000 筆事件；使用 `truncated` / `earliest_cursor` 報告淘汰情況 |
| `conversation` | 分頁讀取的是可變快照；壓縮或進行中的回合可能改變位移量 |

Creature 工作階段在明確關閉、所屬工作區被移除或伺服器停止前會保留；取消執行不會刪除可供延續的歷史。伺服器不會連接其他 KT 程序、匯入任意已儲存對話，也不會在自身重新啟動後自動還原任務或工作階段。

Creature 持久化使用 `<設定環境>/mcp-serve/sessions/<工作區執行環境 instance_id>/` 下的一般 `.kohakutr` 檔案。檔案保留不代表原 MCP 控制代碼仍可使用：這些控制代碼只在目前工作區執行環境的生命週期內有效。

## 修改設定並使其生效

### 儲存設定與重新啟動

再次執行 `setup` 即可修改端點設定。修改既有設定時，未指定的欄位保留原值；首次建立時，未指定的選用欄位採用預設值。精靈中留空保留顯示值，輸入 `-` 清除選用檔案路徑。`--clear-config` 與 `--clear-ngrok-config` 分別清除對應的自訂檔案路徑，恢復預設設定。工作區註冊不屬於 `setup` 的管理範圍，見 [註冊與管理工作區](#註冊與管理工作區)。

**儲存設定不會改變目前執行實例。** 新設定需要停止服務後重新啟動才會生效；重複執行 `start` 只會重用目前實例並報告待生效變更，不會悄悄重新啟動。

例如，改用一個準備好的新公開 origin：

```powershell
kt mcp-serve setup --non-interactive --origin https://new-domain.example
kt mcp-serve status
kt mcp-serve url                 # 服務執行時顯示目前 URL；停止時顯示已儲存 URL
kt mcp-serve url --configured    # 明確取得下一次啟動使用的 URL
kt mcp-serve stop
kt mcp-serve start
```

修改 origin 不會輪替密鑰（輪替密鑰使用 `rotate`，見 [密鑰外洩或端點紀錄損毀](#密鑰外洩或端點紀錄損毀)），但重新啟動後需要更新用戶端中的連線 URL。切換至 `external` 模式會清除已儲存的 ngrok 專用設定；該模式下傳入 ngrok 參數會被拒絕。

### 目前設定與待生效設定

`status` 顯示 `active`、`configured`、`pending_changes` 與 `restart_required`，不顯示憑證。它們分別用於查看目前執行設定、已儲存設定、兩者差異及是否需要重新啟動；其中 `tools_revision` 是工具設定內容的摘要。公開就緒檢查仍針對目前執行實例。

啟動時，端點設定與工具設定的內容會一起寫入綁定執行識別碼的私密 `active.json` 快照。目前實例及其所有通道重試、以及之後才載入的工作區執行環境，都使用這份快照，不會在執行途中採用待生效設定。修改工具設定檔後，`tools_revision` 的差異會出現在 `pending_changes` 中；重新啟動後才生效。

**快照不包含委派目標定義與 ngrok 設定檔的內容。** 委派目標定義在委派實例建立時讀取，修改後影響新建立的實例；ngrok 設定檔可能影響下一次通道重新啟動。狀態比較不偵測這些檔案的內容。

### 並行修改

精靈會檢查設定紀錄是否在開啟後發生變化。如果另一個終端已儲存新設定，或者在此期間執行過 `rotate`，本次儲存會回報衝突並要求重開精靈，不覆寫對方修改。所有檢查都先於單次原子儲存。啟動失敗會保留新設定並回報錯誤，不自動回復。

## 從舊版依工作區端點遷移

舊版本為每個工作區維護獨立的端點與密鑰，紀錄儲存在 `~/.kohakuterrarium/mcp-serve/<workspace-key>/connection.json`。新版 CLI 不再讀取這些紀錄，也不會自動轉換它們；需要用 `migrate` 明確匯入：

```powershell
kt mcp-serve migrate --workspace ./my-project --name myproject
kt mcp-serve start
kt mcp-serve url
```

遷移的前提與效果：

- **舊服務必須已經停止。** 新版 CLI 無法停止舊版啟動的程序；請在升級前用舊版的 `kt mcp-serve stop --workspace PATH` 停止它。舊服務仍在執行時，遷移會回報錯誤並且不做任何修改。
- **目標設定環境必須為空**：還沒有端點設定，也沒有註冊任何工作區。因此一次只能遷移一個舊工作區；其餘舊工作區用 `workspace add` 註冊到同一端點即可，它們各自的舊密鑰不再使用。
- 舊紀錄中的 origin、連線模式、連接埠與 ngrok 設定會被保留；舊工具設定會去掉 `workspace` 欄位，轉換為 `<設定環境>/mcp-serve/endpoint/migrated-tools.json`。該工作區以 `--name` 指定的名稱註冊。
- **遷移會產生新密鑰**，因此連線 URL 會改變，需要在用戶端中更新。舊紀錄只會被讀取，不會被修改或刪除。

舊紀錄不在預設位置時，用 `--legacy-state-dir` 指定舊版的 `mcp-serve` 狀態目錄。

## 疑難排解

監督程序與通道的診斷資訊儲存在 `<設定環境>/mcp-serve/endpoint/` 下的 `server.log`、`tunnel.log`。排查連線問題時，先查看 `kt mcp-serve status --json`，再結合日誌判斷。

| 現象 | 檢查與處理 |
| --- | --- |
| `start` 回傳非零，但本機服務似乎已經執行 | 查看 `state`、`local_ready`、`public_ready`、`tunnel_state` 與最近公開檢查時間；區分公開端點尚未連通、管理通道異常與本機啟動失敗。若監督程序尚未接管就逾時，可增加 `--wait` 後重試 |
| `setup` 成功，但無法從用戶端連線 | `setup` 不驗證公開連通性。確認入口轉送至所選回送連接埠、服務公開就緒，並確認用戶端填入的是完整連線 URL 而不是 origin |
| 本機連接埠被占用 | 檢查占用情況，或用 `setup --port` 儲存其他連接埠；`external` 入口的轉送目標也需相應調整，再啟動服務。多個設定環境需要使用不同連接埠 |
| 回報錯誤 `Global MCP configuration must be an object without workspace` | 工具設定檔現在是全域的，刪除其中的 `workspace` 欄位，改用 `workspace add` 註冊目錄 |
| 工具呼叫回報錯誤 `Unknown workspace` | 名稱拼寫錯誤或尚未註冊；呼叫 `workspaces` 查看已註冊的名稱 |
| `workspaces` 中某個工作區為 `unavailable` | 查看其 `error`：常見原因是目錄已被刪除或移動、外掛載入失敗。修正後下一次呼叫會重新載入；目錄已移動時，移除舊註冊並重新 `workspace add` |
| `workspace remove` 回報錯誤 `Workspace is busy` | 該工作區仍有執行中的呼叫、任務或未關閉的工作階段；先完成或關閉它們，或使用 `--force` |
| 回報錯誤 `Workspace command outcome is unconfirmed` | 命令可能已經執行；先執行 `workspace list` 確認，再決定是否重試，不要直接重複執行 |
| 狀態顯示 `degraded`，或回報錯誤 `Management unavailable` | 本機管理通道異常；既有工具呼叫不受影響，但需要停止並重新啟動端點後才能執行 `workspace` 命令 |
| 修改設定後沒有生效 | 查看 `pending_changes`、`restart_required`，停止後再啟動；重複 `start` 不會重新啟動。若修改的是委派目標定義或 ngrok 設定檔的內容，狀態差異不會偵測到 |
| 新增了委派目標，但用戶端找不到工具 | 修改目標清單後需要重新啟動伺服器，並重新整理用戶端工具清單 |
| 背景任務完成後沒有收到回覆 | 背景完成不會自動喚醒用戶端對話；主動呼叫 `job_status` 或 `job_wait` |
| 委派回傳 busy，但沒有活動中的 MCP 委派 job ID | Creature 可能正在處理自動觸發回合；檢查 `delegation_sessions` 的即時狀態與 `delegation_history` |
| 狀態顯示 `unresponsive` | 執行狀態超過 30 秒未更新，不一定表示工具已停止執行；結合日誌判斷，原因見 [程序與通道復原機制](#程序與通道復原機制) |
| 回報錯誤 `Invalid saved endpoint` | 端點紀錄損毀，見 [密鑰外洩或端點紀錄損毀](#密鑰外洩或端點紀錄損毀) |
| `rotate` 回報錯誤 `MCP instance lock is busy` | 服務仍在執行，先執行 `stop`；如果已經停止，稍後重試 |
| 重新啟動後舊 `job_id` / `session_id` 無法使用 | 伺服器重新啟動會建立新實例，不自動還原舊任務或工作階段；持久化檔案與穩定的連線 URL 不會延長舊 MCP 控制代碼的有效期 |

## 存取與隔離邊界

完整密鑰 URL 是**持有者憑證**，不是 OAuth 或 ChatGPT 帳號身分；持有 URL 的人即可存取該端點上所有已註冊工作區的工具。`setup` 摘要、狀態與生命週期命令的 JSON 輸出會隱藏密鑰，但供人閱讀的就緒啟動輸出與 `url` 命令會主動顯示完整 URL。用戶端只能查看已註冊的工作區，不能遠端註冊、移除工作區或修改設定。

工作區之間的隔離是**執行狀態隔離，不是權限隔離**：每個工作區有獨立的任務、檔案讀取紀錄、`pwd_guard` 放行紀錄與委派工作階段，但它們執行在同一個程序中、使用同一份密鑰，而且 `bash` 等工具本身可以存取工作區以外的路徑。不要把不同工作區當作安全邊界；需要真正隔離的存取範圍時，使用不同的設定環境（不同的密鑰與程序），並考慮作業系統層面的隔離。

同一工作區的所有已通過驗證的用戶端，共享該工作區的讀取紀錄、任務，以及委派工作階段與歷史的存取權。不要把不同用戶端或不同對話當作權限隔離邊界。

委派實例以所選工作區為工作目錄，不同對話共享目錄中的檔案，不建立 worktree 或檔案系統隔離。MCP 不額外增加路徑限制；委派目標的工具、外掛與自動觸發器遵循目標設定，**不受直接 MCP 工具允許清單及其策略約束**。需要的執行限制與沙箱策略應設定在目標定義及其外掛中。

HTTPS 通道在服務商處終止 TLS，不應假設服務商無法看到明文。

### 密鑰外洩或端點紀錄損毀

密鑰外洩或需要定期更換時，停止服務後用 `rotate` 產生新密鑰：

```powershell
kt mcp-serve stop
kt mcp-serve rotate
kt mcp-serve start
kt mcp-serve url
```

`rotate` 替換目前設定環境中端點的密鑰，因此對該端點上的所有工作區同時生效；origin、連線模式、連接埠、設定檔路徑與工作區註冊都保持不變，其他設定環境不受影響。它只能在服務停止時執行，也不會替你停止服務：實例仍在執行時會回報錯誤 `MCP instance lock is busy`，不做任何修改。執行時沒有二次確認，完成後服務仍保持停止。輪替只要求已有有效的端點紀錄，不要求 ngrok 或工具設定等相依項目可用。

`rotate` 的一般輸出與 `--json` 輸出都不包含密鑰或連線 URL；用 `url` 取得新 URL，並更新所有用戶端。重新啟動後，舊 URL 不再通過驗證。建議逐條執行上面的命令，確認 `stop` 與 `rotate` 都成功後再繼續。使用非預設設定環境時，每條命令都要加上相同的 `--home-dir`。

`setup` 仍然不會更換密鑰。如果在輪替前已經開啟了 `setup` 精靈，儲存時會回報設定衝突，需要重新執行 `setup`。

端點紀錄損毀時，`rotate`、`setup` 與其他生命週期命令都會回報錯誤 `Invalid saved endpoint; restore it explicitly`。此時依以下步驟重新產生端點：

1. 執行 `kt mcp-serve stop`。必須先停止服務，再移動紀錄。該命令仍會停止執行中的實例，但結束時可能回報錯誤，可以忽略。
2. 將 `<設定環境>/mcp-serve/endpoint/connection.json` 移出原目錄作為備份。同目錄的 `workspaces.json` 不需要移動，工作區註冊會保留。
3. 重新執行 `kt mcp-serve setup`。這相當於首次設定：需要重新提供 origin、模式、連接埠、`--config` 等設定，儲存時會產生新密鑰。
4. 執行 `kt mcp-serve start`，在用戶端中把舊 URL 替換為新 URL。

如果你有完好的備份，也可以在停止服務後用備份覆寫 `connection.json`，這樣會保留原密鑰與 URL。如果備份中的密鑰可能已經外洩，還原後應立即執行 `rotate`。工作區註冊表本身損毀時，命令會回報錯誤 `Invalid workspace registry`；可以同樣移走 `workspaces.json`，再用 `workspace add` 重新註冊。

## 進階參考

### 命令參數與非互動使用

設定參數屬於 **`setup`**，而不是 `start`：

| 命令 | 主要參數 |
| --- | --- |
| `setup` | `--mode`、`--origin`、`--port`、`--config`、`--ngrok-bin`、`--ngrok-config`、`--clear-config`、`--clear-ngrok-config`、`--non-interactive` |
| `start` | `--wait`，僅控制啟動等待，不接受上述設定參數 |
| `url` | `--configured`，取得下一次啟動使用的 URL |
| `rotate` | 無專用參數；僅在服務停止時替換密鑰，保留其他設定 |
| `workspace add NAME PATH` / `workspace list` / `workspace remove NAME` | `remove` 支援 `--force` |
| `migrate` | `--workspace PATH`、`--name NAME`（皆為必填）、`--legacy-state-dir PATH` |
| 所有子命令 | `--home-dir PATH`，選擇設定環境；預設使用 `KT_CONFIG_DIR` 或 `~/.kohakuterrarium` |
| 除 `url` 外的所有子命令 | `--json`，輸出不含 MCP 密鑰的 JSON |

沒有已儲存的設定時，`start` 會提示先執行 `setup`。腳本中可明確選擇一種連線模式；以下兩行是替代方案，不需要連續執行：

```powershell
kt mcp-serve setup --non-interactive --mode ngrok --origin https://your-fixed-domain.example
kt mcp-serve setup --non-interactive --mode external --origin https://your-domain.example
```

非 TTY 輸入、`--non-interactive` 或 `--json` 會關閉 `setup` 的所有互動提示；缺少必要參數時回傳非零結束碼。互動模式下的命令列參數用來預填精靈。

各命令的結束碼：

| 結束碼 | 情況 |
| --- | --- |
| 0 | 命令成功；對 `start` 而言，表示狀態為 `ready` 且已通過公開就緒檢查。互動式 `setup` 在確認環節選擇不儲存時也回傳 0 |
| 1 | `start` 結束時尚未 `ready`（本機服務可能已在執行）；設定、相依項目、鎖或程序出錯，例如沒有已儲存設定、另一條生命週期命令仍在執行、`stop` 在 20 秒內未能停止實例、對執行中的端點執行 `rotate`、`workspace` 命令被拒絕或結果未確認；互動式 `setup` 被 Ctrl+C 或 EOF 中斷 |
| 2 | 命令列參數不合法 |

使用 `--json` 時，出錯的命令會輸出 `{"error": "..."}`。

儲存前會檢查 origin 與本機相依項目：解析工具設定、在託管模式下確認能找到 ngrok，以及確認明確指定的 ngrok 設定檔可讀取。ngrok 設定檔的內容由 ngrok 在啟動時驗證，本機連接埠是否被占用也在啟動時檢查。

### 程序與通道復原機制

背景監督程序為每個設定環境持有作業系統檔案鎖，單憑舊 PID 不會認定程序歸屬。停止請求與 `workspace` 管理命令都透過私密本機檔案攜帶目前執行識別碼，不透過遠端管理介面傳送；舊的請求不能作用於新一輪實例。管理命令的結果會在本機確認，未確認的命令不會被自動重放。

公開連線故障不會觸發隨機網域回退，也不會建立新的工具實例。託管 ngrok 結束後會以 1–30 秒的有界退避重試；仍在執行的 ngrok 自行處理網路重連。監督程序會定期檢查公開端點的實例身分，不下載任務內容。

僅託管模式會清除 ngrok 子程序繼承的 HTTP 代理環境變數，保留 ngrok 自己的設定，不更改系統代理。歸屬管線讓通道守護程序能在監督程序意外結束時回收自己的 ngrok 子程序。

Windows 上短暫占用檔案的讀取者可能延遲狀態檔案的原子更新。這類診斷寫入失敗不會停止工具執行；執行狀態超過 30 秒未更新時會顯示為 `unresponsive`，而仍被持有的歸屬鎖會阻止重複啟動。

### 委派執行環境與團隊協作範圍

Creature 使用 KT 無介面 I/O，並保留具名輸出與觸發器。Terrarium 配方不是委派目標：團隊任務的關聯、完成與取消需要獨立的協作協定；內部使用 Terrarium 承載 Creature，並不提供這套團隊層級語意。

### 嵌入 ASGI 應用程式

`api.mcp_tools.create_app(config, secret=..., port=..., public_origin=...)` 回傳 ASGI 應用程式，支援兩種模式：

- **註冊工作區模式**：傳入不含 `workspace` 的全域工具設定（`GlobalToolsConfig`）與 `registry=WorkspaceRegistry(...)`，可選 `base_dir`。行為與 `kt mcp-serve` 相同，提供 `workspaces` 工具，其他工具需要 `workspace_id`。
- **固定工作區模式**：傳入綁定 `workspace` 的 `MCPToolsConfig`，不傳 `registry` 與 `base_dir`。只服務這一個目錄，不提供 `workspaces` 工具，工具也不接受 `workspace_id`。

執行其 lifespan，並**關閉宿主存取日誌**。所有請求（包括探索）都需要精確的密鑰路徑。SDK 看到的是已遮蔽的路徑，並檢查允許的 Host/Origin。不要掛載未受保護的副本，也不要同時公開 Studio 管理 API。

## 參閱

- [MCP 用戶端設定](mcp.md)：將外部 MCP 工具接入 Creature。
- [Creature 設定](creatures.md)：定義本機委派目標。
- [子代理](sub-agents.md)：子代理能力與設定。
- [反向代理部署](deployment-reverse-proxy.md)：維護外部 HTTPS 入口。
