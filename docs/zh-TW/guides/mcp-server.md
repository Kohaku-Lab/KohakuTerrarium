---
title: MCP 伺服器
summary: 啟動依工作區管理的 MCP 伺服器，連接外部用戶端，管理工具任務，並視需要啟用本機 Creature 或子代理委派。
tags:
  - guides
  - mcp
  - deployment
---

# MCP 伺服器

MCP 伺服器讓外部 MCP 用戶端使用目前工作區中的 KT 能力。你可以直接呼叫檔案與命令工具，也可以視需要設定本機 Creature 或一次性子代理來承接任務。只使用直接工具時，不會建立 Creature，也不會啟動本機模型。

`kt mcp-serve` 為每個工作區管理一個獨立背景程序、具備驗證機制的 Streamable HTTP 端點，以及選用的 ngrok 通道。本指南介紹的是「讓外部用戶端呼叫 KT」；若要讓 Creature 呼叫其他 MCP 伺服器，請閱讀 [MCP 用戶端設定](mcp.md)。

> **開放前請確認權限範圍。** 預設工具包含檔案寫入與命令執行；工作區是預設執行目錄，**不是沙箱**。完整連線 URL 含存取密鑰，應當視為憑證保管。詳細說明見 [存取與隔離邊界](#存取與隔離邊界)。

首次使用請從 [首次連線設定](#首次連線設定) 開始。已經連通時，可直接查看 [呼叫工具與管理任務](#呼叫工具與管理任務)、[自訂直接工具與外掛](#自訂直接工具與外掛) 或 [委派給本機 Creature 或子代理](#委派給本機-creature-或子代理)；連線或執行異常請見 [疑難排解](#疑難排解)。

## 首次連線設定

### 準備條件與連線模式

安裝包含 `kt mcp-serve` 的 KT 版本，並準備一個穩定的公開 HTTPS 入口。兩種連線模式擇一使用：

| 模式 | 你需要準備 | KT 負責的部分 |
| --- | --- | --- |
| `ngrok` | 已安裝並完成驗證設定的 ngrok、帳號與固定 HTTPS 網域 | 啟動、監督與回收 ngrok 通道程序 |
| `external` | 自行維護的穩定 HTTPS 入口，並將請求轉送至本機回送連接埠 | 只管理本機 MCP 服務，不啟動或停止外部通道 |

本機連接埠預設為 8765。使用 `external` 時，將入口轉送至 `http://127.0.0.1:8765`；選擇其他連接埠時相應調整。即使主機本身可公開存取，也需要 HTTPS 反向代理：KT 只監聽回送位址，不自行終止 TLS。入口部署可參考 [反向代理部署](deployment-reverse-proxy.md)。

精靈中的**公開 HTTPS origin** 是入口的協定、網域及選用連接埠，例如 `https://your-domain.example`，不含 MCP 路徑。它不是稍後要填入用戶端的完整連線 URL。

### 設定並啟動

在要提供給用戶端的工作區中執行：

```powershell
kt mcp-serve setup
kt mcp-serve start
kt mcp-serve url
```

`setup` 開啟互動式設定精靈，依序選擇連線模式、公開 origin、本機連接埠、選用的 MCP 工具設定檔，以及託管模式下的 ngrok 執行檔與設定檔。首次只使用預設工具時，可以不指定工具設定檔。儲存前會顯示變更摘要並要求確認；在精靈中取消或輸入結束（EOF）不會改動原設定。

**`setup` 只儲存設定。** 它不會啟動服務、安裝 ngrok、註冊帳號、分配網域或測試公開連通性。儲存前會檢查本機相依項目；各項檢查的時機見 [命令參數與非互動使用](#命令參數與非互動使用)。

`start` 使用已儲存的設定啟動服務，並等待公開就緒檢查。結束碼 0 表示已透過公開端點完成具驗證的初始化，並確認連到目前實例；非零結束碼不一定表示本機程序未啟動，見 [區分本機就緒與公開就緒](#區分本機就緒與公開就緒)。

### 新增至用戶端

服務就緒後，供人閱讀的啟動輸出會顯示完整連線 URL，也可透過 `kt mcp-serve url` 再次取得。在支援 Streamable HTTP 的外部用戶端中新增 MCP 伺服器，貼上這個**完整 URL**，不要只填入公開 origin。

完整 URL 的形式為 `https://your-domain.example/mcp/<密鑰>`，其中密鑰是首次執行 `setup` 時產生的 43 個字元的隨機字串。

完整 URL 含存取密鑰，不要放入版本控制、一般日誌或公開截圖。用戶端要求的工具呼叫確認仍由用戶端處理，KT 不會繞過確認。

### 驗證首次工具呼叫

連線後，先讓用戶端呼叫不帶 `job_id` 的 `job_status`，確認能取得 `instance_id`；再呼叫 `tree` 查看工作區根目錄，驗證一次唯讀工具操作。這一步不需要啟動本機模型，也不需要寫入檔案。

到這裡應分別確認三個結果：設定已儲存、伺服器公開就緒、用戶端實際能呼叫工具。只看到 `setup` 成功，並不代表後兩步已經完成。

## 日常啟停與狀態

### 啟動、停止與重新取得 URL

設定完成後，日常使用不需要重複執行 `setup`：

```powershell
kt mcp-serve start
kt mcp-serve status
kt mcp-serve url
kt mcp-serve stop
```

重複或同時執行 `start` 會重用現有實例，不會重複啟動，也不會自動套用[待生效設定](#目前設定與待生效設定)。`stop` 會取消所屬任務、關閉本機監聽並回收所管理的 ngrok 程序，保留連線設定，不刪除雲端資源。再次啟動會重用連線設定，但會建立新的執行實例；任務與工作階段的生命週期見 [呼叫工具與管理任務](#呼叫工具與管理任務)。

監督程序不是開機服務。當機或重新啟動電腦後，需要重新執行 `start`。

### 管理其他工作區

預設管理目前目錄。所有子命令都可以加上 `--workspace PATH`，例如：

```powershell
kt mcp-serve status --workspace ./another-project
```

工作區身分會解析符號連結與 Windows 路徑大小寫。移動目錄會產生不同的身分，舊 URL 不會自動綁定至新位置。連線紀錄損毀時，所有子命令（包括 `setup`）都會拒絕繼續操作。目前版本不提供工作區遷移命令；更換密鑰或修復紀錄的方法見 [密鑰外洩或連線紀錄損毀](#密鑰外洩或連線紀錄損毀)。

### 區分本機就緒與公開就緒

`start` 預設最多等待 30 秒，可透過 `--wait` 指定 1–120 秒的等待時間。例如：

```powershell
kt mcp-serve start --wait 60
kt mcp-serve status --json
```

結束碼 1 可能表示本機服務已經執行，但公開端點尚未連通。此時查看狀態中的 `local_ready`、`public_ready`、`tunnel_state` 與最近公開檢查時間，不要只憑啟動命令的結束碼判斷程序是否存在。公開就緒狀態始終針對正在執行的實例，而不是尚未生效的設定。

`status --json` 中與就緒相關的欄位：

| 欄位 | 取值與含義 |
| --- | --- |
| `state` | `starting` 啟動中；`ready` 本機已監聽，且最近一次公開檢查通過；`offline` 本機已執行，但公開檢查未通過或通道未執行；`failed` 啟動或執行出錯，原因見 `error`；`stopped` 未執行；`unresponsive` 程序仍持有實例鎖，但執行狀態超過 30 秒未更新或無法讀取 |
| `local_ready` / `public_ready` | 本機監聽是否就緒 / 最近一次公開檢查是否通過 |
| `tunnel_state` | `ngrok` 模式下為 `starting`、`connecting`、`online`、`reconnecting` 或 `stopped`；`external` 模式下固定為 `external` |
| `public_checked_at` | 最近一次公開檢查的 Unix 時間戳記（秒）。檢查通過後約每 10 秒複查一次，未通過時約每 2 秒重試 |
| `error` | 最近一次錯誤訊息，不含密鑰 |
| `record_path` | 連線紀錄 `connection.json` 的完整路徑 |

如果等待到期時監督程序尚未接管實例，啟動命令會回收該子程序並回報錯誤。較慢的主機可增加 `--wait` 後重試。

## 呼叫工具與管理任務

### 預設工具與一般呼叫

不指定工具設定檔時，預設提供九個直接工具：`read`、`write`、`edit`、`multi_edit`、`glob`、`grep`、`tree`、`bash` 與 `python`。這些工具共用工作區執行上下文。

前景呼叫回傳 KT Executor 的 `job_id`、輸出、錯誤、結束碼與中繼資料。`job_id` 識別一次執行，後續查詢、等待或取消都使用該 ID，不需要重新執行原操作。

讀取圖片檔案時，結果以 MCP 圖片內容回傳。讀取 PDF 時只回傳文字，不回傳算繪後的頁面圖片。

### 背景執行、查詢與等待

對 `bash` / `python` 傳入 `run_in_background: true` 會立即回傳**同一個 KT job ID**，執行繼續進行。以下四個任務工具用於查詢與控制這些任務：

| 工具 | 行為 |
| --- | --- |
| `job_status` | 讀取單個任務；省略 `job_id` 時列出保留任務及 `instance_id` |
| `job_wait` | 等待 0–60 秒，預設 10 秒；逾時回傳目前狀態 |
| `job_cancel` | 取消所屬的執行中任務；Creature 委派的取消範圍更大，見 [補充輸入與取消](#補充輸入與取消) |
| `job_promote` | 將前景呼叫釋放至背景，保留原 job ID，不重複執行 |

任務不會僅因經過一段時間就自動轉背景。等待逾時、等待請求中斷連線或被取消，都不會取消所屬任務；需要停止執行時，應明確使用 `job_cancel`。

**背景完成不會自動喚醒 ChatGPT 對話。** 用戶端需要主動查詢或等待結果。本機委派也使用這些任務工具，但委派提交本身已經非同步，無須再呼叫 `job_promote`。

### 取消、重試與結果保留

任務結果與 MCP 查詢是否成功是兩件事。即使任務失敗或被取消，讀取或等待其保留紀錄仍是成功的 MCP 呼叫；`state`、`error` 與 `exit_code` 描述的是任務結果。未知任務、無效查詢參數與前景執行失敗仍屬於 MCP 錯誤。

變更操作的 HTTP 回覆遺失後，不要立即重複提交。先透過 `job_status` 查詢；涉及委派時，同時檢查工作階段狀態，避免把回覆遺失誤當成任務未執行。

伺服器最多保留 100 個已完成任務，直接工具任務與委派任務採用相同的有界保留規則。正常停止伺服器會取消所屬任務；重新啟動會建立新實例，不會還原舊任務，舊 ID 不會匹配新任務。連線 URL 的身分與這些執行狀態互相獨立，URL 不變不代表舊任務仍然存在。

## 自訂直接工具與外掛

### 設定檔與路徑規則

設定分為幾個不同層次，不要把它們當成同一種檔案：

| 設定 | 負責什麼 | 如何指定 |
| --- | --- | --- |
| 連線設定 | 工作區身分、密鑰、公開 origin、連接埠、連線模式及外部設定檔路徑 | 由 `kt mcp-serve setup` 管理 |
| MCP 工具設定檔 | 直接工具、執行外掛與委派目標註冊 | `setup --config ./mcp.yaml` |
| Creature 或子代理定義 | 委派目標自己的模型、工具、外掛與執行限制 | MCP 工具設定中的 `delegation.<別名>.config` |
| ngrok 設定檔 | ngrok 本身的設定 | 託管模式下的 `setup --ngrok-config PATH` |

首次執行 `setup` 時，連線設定儲存至 `~/.kohakuterrarium/mcp-serve/<workspace-key>/connection.json`，後續啟動會重用該紀錄。自訂直接工具時，另建獨立的 YAML 或 JSON 檔案，而不是把一般 Creature 設定直接交給 `--config`。

以下範例統一將 `mcp.yaml` 放在工作區根目錄，並在該目錄執行 CLI，因此使用 `workspace: .`。`workspace` 必須存在，相對路徑以**設定檔所在目錄**為基準；解析後必須與 CLI 選定的工作區一致，否則會拒絕啟動。

### 選擇工具與設定執行參數

工作區根目錄下的 `mcp.yaml`：

```yaml
name: KT tools
workspace: .
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

省略 `tools` 會啟用前述九個工具。工具名稱必須唯一，`type` 必須為 `builtin`（預設值）。支援 `max_output` 及各工具宣告的執行時選項：`timeout` 適用於 bash/Python，`env` 適用於 bash。不接受逐工具 `working_dir`，因為目錄由共享執行上下文提供。

MCP 工具設定頂層不接受控制器通知設定、LLM 設定、提示詞、觸發器、compact 或 AgentConfig 繼承；這些欄位會被明確拒絕，而不是悄悄忽略。需要本機模型執行任務時，應使用下一節的委派目標設定。

目錄是預設執行位置，**不是沙箱**。KT 原有的先讀後寫、過期讀取檢查、路徑保護與執行策略仍然適用。`pwd_guard` 控制對工作區外路徑的存取。預設的 `warn` 會攔截首次存取某個工作區外路徑的操作並回傳警告，對同一路徑再次執行即放行；`block` 一律拒絕；`off` 不檢查。放行紀錄只在目前執行實例內有效，並由該實例的所有用戶端共享。

### 執行外掛及其能力限制

直接工具的執行外掛使用一般 `name`、`type`、`module`、`class` 與 `options` 設定，只支援執行側能力：載入與卸載、分派、執行前後鉤子、執行時服務與轉背景。外掛載入失敗會中止啟動；覆寫 LLM、Agent 生命週期、事件、compact、提示詞、可見性、命令或終止鉤子的外掛會被拒絕。

這些外掛在執行時能取得的上下文只有工作目錄、名稱與實例 ID，沒有宿主 Agent、Controller、工作階段持久化、模型切換或子代理建立能力。自訂外掛需要遵守此約定；外掛屬於受信任的本機程式碼。這裡的限制針對直接工具執行環境，不取代委派目標自己的外掛設定。

## 委派給本機 Creature 或子代理

本節是選用能力。只使用直接工具時，無須準備模型設定或註冊委派目標。

> 委派目標共享工作區檔案，但使用自己的工具與外掛策略。直接 MCP 工具允許清單不會限制委派能力；啟用前請核對目標定義中的權限與執行限制，詳見 [存取與隔離邊界](#存取與隔離邊界)。

### 選擇目標類型

| 類型 | 適用方式 | 對話生命週期 |
| --- | --- | --- |
| `creature` | 需要多輪延續，或使用 Creature 的工具、外掛與自動觸發器 | 可用 `session_id` 延續；已關閉的工作階段不能延續 |
| `subagent` | 一次性任務，無須父 Creature | 執行中可補充輸入，完成後不能在同一對話繼續；新任務會建立新子代理 |

目標只從本機設定中註冊。用戶端可以選擇別名，但不能提交設定路徑、內嵌定義，或覆寫模型與工具設定。僅註冊目標不會立即建立實例或啟動模型。

### 註冊目標並準備模型設定

先用一般 KT 命令設定本機模型憑證與模型設定，並準備好目標定義。Creature 使用一般 KT 設定格式；下面的 `coder` 範例要求已安裝包含該定義的 `@kt-biome` 套件。

在前述 `mcp.yaml` 中保留 `workspace: .`，新增 `delegation` 欄位。下面只展示工作區與委派部分；既有的 `tools`、`plugins` 等欄位可以保留：

```yaml
workspace: .
delegation:
  coder:
    kind: creature
    config: "@kt-biome/creatures/swe"
    description: "在此工作區實作並驗證修改"
  reviewer:
    kind: subagent
    config: ./reviewer.yaml
    description: "審查具體修改並回報發現"
```

目標定義的相對路徑以 MCP 工具設定檔所在目錄為基準，已安裝的 `@package/...` 引用沿用 KT 套件解析規則。定義在委派實例建立時載入；錯誤定義會使該任務失敗，不會悄悄捨棄已設定的能力。

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

註冊委派目標後，會增加六個工具：

| 工具 | 用途 |
| --- | --- |
| `delegation_targets` | 列出目標別名與描述，不啟動模型 |
| `delegate` | 提交 `target`、`prompt`，可用 `session_id` 延續 Creature 工作階段 |
| `delegation_send` | 向執行中的委派任務（依 `job_id`）補充資訊 |
| `delegation_sessions` | 列出本伺服器擁有的工作階段、忙碌狀態與目前委派任務 |
| `delegation_history` | 分頁讀取工作階段活動或目前公開對話快照 |
| `delegation_close` | 停止並關閉本伺服器擁有的工作階段，保留可讀取的歷史 |

一次典型的 Creature 委派流程如下。以下是 MCP 工具呼叫示意，不是終端命令：

1. 呼叫 `delegation_targets`，選擇目標別名。
2. 呼叫 `delegate(target="coder", prompt="調查失敗的測試")`，儲存回傳的 `job_id` 與 `session_id`。前者識別本次執行，後者識別對話；提交會在模型執行前回傳。
3. 使用 `job_status` / `job_wait` 取得結果，或用 `delegation_history` 查看活動。等待與重試遵循前述 [任務管理規則](#呼叫工具與管理任務)，無須 `job_promote`。
4. 本回合結束後，用 `delegate(target="coder", session_id=..., prompt="套用修正")` 繼續對話。省略 `session_id` 會建立獨立對話；不再需要工作階段時呼叫 `delegation_close`。

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
| 停止 MCP 伺服器 | 取消伺服器所屬任務，結束其工作階段生命週期 | 伺服器重新啟動後不會自動還原任務或工作階段，舊 MCP 控制代碼不再有效 |

> 對**已完成任務**呼叫 `job_cancel` 不會產生效果。要停止 Creature 工作階段中剩餘的背景工作，應使用 `delegation_close`。取消不會回復檔案修改，也不承諾回收任意脫離 KT 管理的作業系統程序。

取消 Creature 後，明確延續同一個、本伺服器擁有的工作階段，會從已持久化的工作階段檔案重建執行環境（見 [查看歷史與工作階段生命週期](#查看歷史與工作階段生命週期)）。這與「關閉工作階段」或「重新啟動伺服器」不同，不應混為一談。

### 查看歷史與工作階段生命週期

`delegation_history(session_id=..., view="events")` 查看活動；`view="conversation"` 查看公開訊息與完整保留的工具結果。兩種檢視透過 `cursor` 與 `limit`（1–200）分頁，但保留與分頁方式不同：

| 檢視 | 需要注意的限制 |
| --- | --- |
| `events` | 只保留最新 2,000 筆事件；使用 `truncated` / `earliest_cursor` 報告淘汰情況 |
| `conversation` | 分頁讀取的是可變快照；壓縮或進行中的回合可能改變位移量 |

Creature 工作階段在明確關閉或伺服器停止前會保留；取消執行不會刪除可供延續的歷史。伺服器不會連接其他 KT 程序、匯入任意已儲存對話，也不會在自身重新啟動後自動還原任務或工作階段。

Creature 持久化使用 `~/.kohakuterrarium/mcp-serve/sessions/<instance-id>/` 下的一般 `.kohakutr` 檔案。檔案保留不代表原 MCP 控制代碼仍可使用：這些控制代碼只在目前伺服器生命週期內有效。

## 修改設定並使其生效

### 儲存設定與重新啟動

再次執行 `setup` 即可修改連線設定。修改既有連線時，未指定的欄位保留原值；首次建立連線時，未指定的選用欄位採用預設值。精靈中留空保留顯示值，輸入 `-` 清除選用檔案路徑。`--clear-config` 與 `--clear-ngrok-config` 分別清除對應的自訂檔案路徑，恢復預設設定。

**儲存設定不會改變目前執行實例。** 新設定需要停止服務後重新啟動才會生效；重複執行 `start` 只會重用目前實例並報告待生效變更，不會悄悄重新啟動。

例如，既有連線改用一個準備好的新公開 origin：

```powershell
kt mcp-serve setup --non-interactive --origin https://new-domain.example
kt mcp-serve status
kt mcp-serve url                 # 服務執行時顯示目前 URL；停止時顯示已儲存 URL
kt mcp-serve url --configured    # 明確取得下一次啟動使用的 URL
kt mcp-serve stop
kt mcp-serve start
```

修改 origin 不會輪替密鑰（輪替密鑰使用 `rotate`，見 [密鑰外洩或連線紀錄損毀](#密鑰外洩或連線紀錄損毀)），但重新啟動後需要更新用戶端中的連線 URL。切換至 `external` 模式會清除已儲存的 ngrok 專用設定；該模式下傳入 ngrok 參數會被拒絕。

### 目前設定與待生效設定

`status` 顯示 `active`、`configured`、`pending_changes` 與 `restart_required`，不顯示憑證。它們分別用於查看目前執行設定、已儲存設定、兩者差異及是否需要重新啟動。公開就緒檢查仍針對目前執行實例。

目前實例及其所有通道重試使用綁定執行識別碼的私密 `active.json` 快照，不會在任務中途採用待生效設定。

**快照只凍結 `setup` 管理的欄位，不凍結所引用檔案的內容。** 修改 MCP 工具設定檔，會在下一次工具程序啟動時生效；修改 ngrok 設定檔可能影響下一次通道重新啟動。狀態比較不偵測、也不保證凍結這些檔案的內容，因此沒有 `pending_changes` 不代表外部檔案沒有修改。委派目標定義的載入時機見 [註冊目標並準備模型設定](#註冊目標並準備模型設定)。

### 並行修改與升級

精靈會檢查設定紀錄是否在開啟後發生變化。如果另一個終端已儲存新設定，本次儲存會回報衝突並要求重開精靈，不覆寫對方修改。所有檢查都先於單次原子儲存。啟動失敗會保留新設定並回報錯誤，不自動回復。

既有連線紀錄無須重新設定即可讀取。較舊 CLI 啟動的程序沒有 active 快照，需要先停止並重新啟動一次，再使用執行中設定編輯或透過新版 CLI 取得執行 URL。升級不會產生新的身分或密鑰。

## 疑難排解

監督程序與通道的診斷資訊儲存在連線紀錄旁，即 `~/.kohakuterrarium/mcp-serve/<workspace-key>/` 下的 `server.log`、`tunnel.log`。排查連線問題時，先查看 `kt mcp-serve status --json`，再結合日誌判斷。

| 現象 | 檢查與處理 |
| --- | --- |
| `start` 回傳非零，但本機服務似乎已經執行 | 查看 `local_ready`、`public_ready`、`tunnel_state` 與最近公開檢查時間；區分公開端點尚未連通與本機啟動失敗。若監督程序尚未接管就逾時，可增加 `--wait` 後重試 |
| `setup` 成功，但無法從用戶端連線 | `setup` 不驗證公開連通性。確認入口轉送至所選回送連接埠、服務公開就緒，並確認用戶端填入的是完整連線 URL 而不是 origin |
| 本機連接埠被占用 | 檢查占用情況，或用 `setup --port` 儲存其他連接埠；`external` 入口的轉送目標也需相應調整，再啟動服務 |
| 回報錯誤 `Tool configuration belongs to a different workspace` | 對照 CLI 的目前目錄或 `--workspace`，檢查 `mcp.yaml` 中依檔案所在目錄解析的 `workspace`；兩者必須指向同一工作區 |
| 修改設定後沒有生效 | 查看 `pending_changes`、`restart_required`，停止後再啟動；重複 `start` 不會重新啟動。若修改的是所引用檔案的內容，狀態差異不會偵測到 |
| 新增了委派目標，但用戶端找不到工具 | 修改目標清單後需要重新啟動伺服器，並重新整理用戶端工具清單 |
| 背景任務完成後沒有收到回覆 | 背景完成不會自動喚醒用戶端對話；主動呼叫 `job_status` 或 `job_wait` |
| 委派回傳 busy，但沒有活動中的 MCP 委派 job ID | Creature 可能正在處理自動觸發回合；檢查 `delegation_sessions` 的即時狀態與 `delegation_history` |
| 狀態顯示 `unresponsive` | 執行狀態超過 30 秒未更新，不一定表示工具已停止執行；結合日誌判斷，原因見 [程序與通道復原機制](#程序與通道復原機制) |
| 回報錯誤 `Missing or invalid saved connection` | 連線紀錄損毀，見 [密鑰外洩或連線紀錄損毀](#密鑰外洩或連線紀錄損毀) |
| `rotate` 回報錯誤 `MCP instance lock is busy` | 服務仍在執行，先執行 `stop`；如果已經停止，稍後重試 |
| 重新啟動後舊 `job_id` / `session_id` 無法使用 | 伺服器重新啟動會建立新實例，不自動還原舊任務或工作階段；持久化檔案與穩定的連線 URL 不會延長舊 MCP 控制代碼的有效期 |

## 存取與隔離邊界

完整密鑰 URL 是**持有者憑證**，不是 OAuth 或 ChatGPT 帳號身分；持有 URL 的人即可存取該實例的工具。`setup` 摘要、狀態與生命週期命令的 JSON 輸出會隱藏密鑰，但供人閱讀的就緒啟動輸出與 `url` 命令會主動顯示完整 URL。

同一伺服器的所有已通過驗證的用戶端，共享直接工具的讀取歷史、工具、外掛、任務，以及委派工作階段與歷史的存取權；不同伺服器實例的狀態互相獨立。不要把不同用戶端或不同對話當作權限隔離邊界。

委派實例繼承 MCP 工作區作為工作目錄，不同對話共享目錄中的檔案，不建立 worktree 或檔案系統隔離。MCP 不額外增加路徑限制；委派目標的工具、外掛與自動觸發器遵循目標設定，**不受直接 MCP 工具允許清單及其策略約束**。需要的執行限制與沙箱策略應設定在目標定義及其外掛中。

HTTPS 通道在服務商處終止 TLS，不應假設服務商無法看到明文。

### 密鑰外洩或連線紀錄損毀

密鑰外洩或需要定期更換時，停止服務後用 `rotate` 產生新密鑰：

```powershell
kt mcp-serve stop
kt mcp-serve rotate
kt mcp-serve start
kt mcp-serve url
```

`rotate` 只替換目前工作區的密鑰，origin、連線模式、連接埠與設定檔路徑等其他設定保持不變，其他工作區不受影響。它只能在服務停止時執行，也不會替你停止服務：實例仍在執行時會回報錯誤 `MCP instance lock is busy`，不做任何修改。執行時沒有二次確認，完成後服務仍保持停止。輪替只要求已有有效的連線紀錄，不要求 ngrok 或工具設定等相依項目可用。

`rotate` 的一般輸出與 `--json` 輸出都不包含密鑰或連線 URL；用 `url` 取得新 URL，並更新所有用戶端。重新啟動後，舊 URL 不再通過驗證；上一次執行留下的 `active.json` 快照也會在啟動時被替換。建議逐條執行上面的命令，確認 `stop` 與 `rotate` 都成功後再繼續。管理其他工作區時，每條命令都要加上相同的 `--workspace`。

`setup` 仍然不會更換密鑰。如果在輪替前已經開啟了 `setup` 精靈，儲存時會回報設定衝突，需要重新執行 `setup`。

連線紀錄損毀時，`rotate`、`setup` 與其他子命令都會回報錯誤 `Missing or invalid saved connection`。此時依以下步驟重新產生連線：

1. 執行 `kt mcp-serve stop`。必須先停止服務，再移動紀錄。該命令仍會停止執行中的實例，但結束時可能回報錯誤，可以忽略。
2. 將 `connection.json` 移出原目錄作為備份。紀錄損毀時無法查詢狀態，檔案位於 `~/.kohakuterrarium/mcp-serve/<workspace-key>/`。
3. 重新執行 `kt mcp-serve setup`。這相當於首次設定：需要重新提供 origin、模式、連接埠、`--config` 等設定，儲存時會產生新密鑰。
4. 執行 `kt mcp-serve start`，在用戶端中把舊 URL 替換為新 URL。

如果你有完好的備份，也可以在停止服務後用備份覆寫 `connection.json`，這樣會保留原密鑰與 URL。如果備份中的密鑰可能已經外洩，還原後應立即執行 `rotate`。

## 進階參考

### 命令參數與非互動使用

設定參數屬於 **`setup`**，而不是 `start`：

| 命令 | 主要參數 |
| --- | --- |
| `setup` | `--mode`、`--origin`、`--port`、`--config`、`--ngrok-bin`、`--ngrok-config`、`--clear-config`、`--clear-ngrok-config`、`--non-interactive` |
| `start` | `--wait`，僅控制啟動等待，不接受上述設定參數 |
| `url` | `--configured`，取得下一次啟動使用的 URL |
| `rotate` | 無專用參數；僅在服務停止時替換密鑰，保留其他設定 |
| 所有子命令 | `--workspace PATH` |
| `setup` / `start` / `stop` / `status` / `rotate` | `--json`，輸出不含 MCP 密鑰的 JSON |

沒有已儲存的設定時，`start` 會提示先執行 `setup`。腳本中可明確選擇一種連線模式；以下兩行是替代方案，不需要連續執行：

```powershell
kt mcp-serve setup --non-interactive --mode ngrok --origin https://your-fixed-domain.example
kt mcp-serve setup --non-interactive --mode external --origin https://your-domain.example
```

非 TTY 輸入、`--non-interactive` 或 `--json` 會關閉 `setup` 的所有互動提示；缺少必要參數時回傳非零結束碼。互動模式下的命令列參數用來預填精靈。

各命令的結束碼：

| 結束碼 | 情況 |
| --- | --- |
| 0 | 命令成功；對 `start` 而言，表示已通過公開就緒檢查。互動式 `setup` 在確認環節選擇不儲存時也回傳 0 |
| 1 | `start` 結束時公開端點尚未就緒（本機服務可能已在執行）；設定、相依項目、鎖或程序出錯，例如沒有已儲存設定、另一條生命週期命令仍在執行、`stop` 在 20 秒內未能停止實例、對執行中的工作區執行 `rotate`；互動式 `setup` 被 Ctrl+C 或 EOF 中斷 |
| 2 | 命令列參數不合法 |

使用 `--json` 時，出錯的命令會輸出 `{"error": "..."}`。

儲存前會檢查 origin 與本機相依項目：解析工具設定、在託管模式下確認能找到 ngrok，以及確認明確指定的 ngrok 設定檔可讀取。ngrok 設定檔的內容由 ngrok 在啟動時驗證，本機連接埠是否被占用也在啟動時檢查。

### 程序與通道復原機制

背景監督程序為每個工作區持有作業系統檔案鎖，單憑舊 PID 不會認定程序歸屬。停止請求透過私密本機檔案攜帶目前執行識別碼，不透過遠端管理介面傳送；舊的停止請求不能停止新一輪實例。

公開連線故障不會觸發隨機網域回退，也不會建立新的工具實例。託管 ngrok 結束後會以 1–30 秒的有界退避重試；仍在執行的 ngrok 自行處理網路重連。監督程序會定期檢查公開端點的實例身分，不下載任務內容。

僅託管模式會清除 ngrok 子程序繼承的 HTTP 代理環境變數，保留 ngrok 自己的設定，不更改系統代理。歸屬管線讓通道守護程序能在監督程序意外結束時回收自己的 ngrok 子程序。

Windows 上短暫占用檔案的讀取者可能延遲狀態檔案的原子更新。這類診斷寫入失敗不會停止工具執行；執行狀態超過 30 秒未更新時會顯示為 `unresponsive`，而仍被持有的歸屬鎖會阻止重複啟動。

### 委派執行環境與團隊協作範圍

Creature 使用 KT 無介面 I/O，並保留具名輸出與觸發器。Terrarium 配方不是委派目標：團隊任務的關聯、完成與取消需要獨立的協作協定；內部使用 Terrarium 承載 Creature，並不提供這套團隊層級語意。

### 嵌入 ASGI 應用程式

`api.mcp_tools.create_app(config, secret=..., port=..., public_origin=...)` 回傳 ASGI 應用程式。執行其 lifespan，並**關閉宿主存取日誌**。所有請求（包括探索）都需要精確的密鑰路徑。SDK 看到的是已遮蔽的路徑，並檢查允許的 Host/Origin。不要掛載未受保護的副本，也不要同時公開 Studio 管理 API。

## 參閱

- [MCP 用戶端設定](mcp.md)：將外部 MCP 工具接入 Creature。
- [Creature 設定](creatures.md)：定義本機委派目標。
- [子代理](sub-agents.md)：子代理能力與設定。
- [反向代理部署](deployment-reverse-proxy.md)：維護外部 HTTPS 入口。
