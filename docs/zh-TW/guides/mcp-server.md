---
title: MCP 伺服器
summary: 設定依工作區管理的 MCP 伺服器，向外部用戶端提供 KT 工具，並將任務委派給本機 Creature 或子代理。
tags:
  - guides
  - mcp
  - deployment
---

# MCP 伺服器

獨立 MCP 伺服器向外部 MCP 用戶端提供 KT 工具。未設定委派目標時，它不會建立 Creature，也不會啟動本機模型。設定委派目標後，用戶端還可以呼叫本機 Creature 與獨立的一次性子代理。

`kt mcp-serve` 為每個工作區管理一個獨立背景程序、具備驗證機制的 Streamable HTTP 端點，以及選用的 ngrok 通道。

## 設定一次，日常啟動與停止

安裝包含 `kt mcp-serve` 的 KT 版本，然後選擇公開連線方式：使用託管 ngrok 時，需要先準備帳號與固定 HTTPS 網域；已有穩定 HTTPS 入口時，則使用 external 模式。在要提供給用戶端的工作區中執行：

```powershell
kt mcp-serve setup
kt mcp-serve start
kt mcp-serve status
kt mcp-serve stop
kt mcp-serve start
```

`setup` 在互動式終端中開啟設定精靈，用來選擇連線模式、公開 HTTPS origin、本機連接埠（預設 8765）、選用的工具設定檔，以及託管模式下的 ngrok 執行檔與設定檔。儲存前會顯示變更摘要並要求確認。取消或輸入結束（EOF）不會改動原設定。

Setup 只儲存設定，不啟動服務，也不安裝 ngrok、註冊帳號、分配網域或測試公開端點的連通性。儲存前會檢查 origin 與本機相依項目：解析工具設定、確認能找到 ngrok，以及確認明確指定的 ngrok 設定檔可讀取。ngrok 設定檔的內容由 ngrok 在啟動時驗證；本機連接埠是否被占用也在啟動時檢查。

腳本中可使用明確參數：

```powershell
kt mcp-serve setup --non-interactive --mode ngrok --origin https://your-fixed-domain.example
kt mcp-serve setup --non-interactive --mode external --origin https://your-domain.example
```

`--ngrok-bin`、`--ngrok-config`、`--port` 與 `--config` 都屬於 **setup**。`start` 只接受 `--wait` 等生命週期參數，不接受設定參數；沒有已儲存的設定時，啟動命令會提示先執行 setup。

非 TTY 輸入、`--non-interactive` 或 `--json` 會關閉所有互動提示；缺少必要參數時回傳非零結束碼。互動模式下的命令列參數用來預填精靈。JSON 輸出不包含 MCP 密鑰。

首次 setup 將正規化的工作區路徑、公開 origin、隨機密鑰、本機連接埠、連線模式與選用的工具設定路徑儲存至 `~/.kohakuterrarium/mcp-serve/<workspace-key>/connection.json`，後續啟動會重用這些設定。服務就緒後，供人閱讀的啟動輸出會顯示完整連線 URL，方便主動複製；也可用 `kt mcp-serve url` 再次查看。請將此 URL 視為私密憑證。Setup 摘要、狀態與生命週期命令的 JSON 輸出會隱藏密鑰。

所有命令都可加 `--workspace PATH` 管理另一個目錄。工作區身分會解析符號連結與 Windows 路徑大小寫。移動目錄會產生不同的身分，舊 URL 不會自動綁定至新位置。連線紀錄損毀時會拒絕繼續操作，需要明確還原；目前版本不提供工作區遷移或密鑰輪替命令。

背景監督程序為每個工作區持有作業系統檔案鎖。同時或重複執行 `start` 會重用現有實例，單憑舊 PID 不會認定程序歸屬。停止請求透過私密本機檔案攜帶目前執行識別碼，不透過遠端管理介面傳送；舊的停止請求不能停止新一輪實例。`stop` 會取消所屬任務、關閉本機監聽並回收所管理的 ngrok 程序，保留連線設定，不刪除雲端資源。

`start` 預設等待最多 30 秒，直到透過公開端點完成身分驗證與初始化，並確認回傳的正是目前實例；可用 `--wait 1..120` 調整。結束碼 0 表示公開端點就緒已驗證。結束碼 1 也可能表示本機服務已執行、但公開端點尚未連通：此時查看 `status` 中的 `local_ready`、`public_ready`、`tunnel_state` 與最近公開端點檢查時間。

如果等待到期時監督程序尚未接管實例，啟動命令會回收該子程序並回報錯誤。較慢的主機可增加 `--wait` 後重試。

公開連線故障不會觸發隨機網域回退，也不會建立新的工具實例。託管 ngrok 結束後會以 1–30 秒的有界退避重試；仍在執行的 ngrok 自行處理網路重連。監督程序會定期檢查公開端點的實例身分，不下載任務內容。

若使用獨立維護的穩定入口：

```powershell
kt mcp-serve setup --non-interactive --mode external --origin https://your-domain.example
kt mcp-serve start
```

將該入口轉送至選定的本機回送連接埠。KT 不會啟動或停止外部通道。即使主機本身可公開存取，也需要 HTTPS 反向代理：KT 只監聽回送位址，不自行終止 TLS。僅託管模式會清除 ngrok 子程序繼承的 HTTP 代理環境變數，保留 ngrok 自己的設定，不更改系統代理。

監督程序與通道的診斷資訊儲存在連線紀錄旁的 `server.log`、`tunnel.log`。歸屬管線讓通道守護程序能在監督程序意外結束時回收自己的 ngrok 子程序。監督程序本身不是開機服務：當機或重新啟動電腦後，需要重新執行 `start`。

Windows 上短暫占用檔案的讀取者可能延遲狀態檔案的原子更新。這類診斷寫入失敗不會停止工具執行；心跳長時間未更新時狀態會變成 `unresponsive`，而仍被持有的歸屬鎖會阻止重複啟動。

## 執行期間修改已儲存設定

再次執行 setup 即可修改設定。省略的欄位保留原值，新設定使用預設值。`--clear-config` 與 `--clear-ngrok-config` 分別恢復相應預設設定；精靈中留空保留顯示值，輸入 `-` 清除選用檔案路徑。切換至 external 模式會清除已儲存的 ngrok 專用設定，該模式下傳入 ngrok 參數會被拒絕。修改 origin 不會輪替密鑰，但會提示更新 ChatGPT 中的連線 URL。

服務執行時可以儲存新設定。目前實例及其所有通道重試使用綁定執行識別碼的私密 `active.json` 快照，不會在任務中途採用待生效設定。新設定在下一次實例啟動時生效。`status` 顯示 `active`、`configured`、`pending_changes` 與 `restart_required`，不顯示憑證。公開端點就緒狀態始終針對正在執行的實例。重複 `start` 會重用該實例並報告待生效變更，不會悄悄重新啟動。

```powershell
kt mcp-serve setup --non-interactive --origin https://new-domain.example
kt mcp-serve status
kt mcp-serve url                 # running URL; saved URL when stopped
kt mcp-serve url --configured    # explicitly copy the next-start URL
kt mcp-serve stop
kt mcp-serve start
```

精靈會檢查設定紀錄是否在開啟後發生變化。如果另一個終端已儲存新設定，本次儲存會回報衝突並要求重開精靈，不覆寫對方修改。所有檢查都先於單次原子儲存。啟動失敗會保留新設定並報告錯誤，不自動回復。

快照只凍結 **setup 管理的欄位**，不凍結外部檔案內容。修改被引用的工具設定檔，會在下一次工具程序啟動時生效；修改 ngrok 設定檔可能影響下一次通道重新啟動。狀態比較不偵測、也不保證凍結這些檔案的內容。

已有連線紀錄無須重新設定即可讀取。較舊 CLI 啟動的程序沒有 active 快照，需要先停止並重新啟動一次，再使用執行中設定編輯或透過新版 CLI 取得執行 URL。升級不會產生新的身分或密鑰。

## 設定

不指定 `--config` 時，下列九個工具使用預設設定。若要自訂，在 setup 中透過 `--config` 指定獨立 YAML 或 JSON 檔案：

```yaml
name: KT tools
workspace: ./work
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

## 委派給本機 Creature 或子代理

在同一個由 `setup --config` 指定的設定檔中註冊目標：

```yaml
workspace: ./work
delegation:
  coder:
    kind: creature
    config: "@kt-biome/creatures/swe"
    description: "Implement and verify changes in this workspace"
  reviewer:
    kind: subagent
    config: ./reviewer.yaml
    description: "Review a concrete change and report findings"
```

目標清單來自本機設定。用戶端只能選擇別名，不能提交設定路徑、內嵌定義，或覆寫模型與工具設定。相對路徑以 MCP 設定檔所在目錄為基準，已安裝的 `@package/...` 引用沿用 KT 套件解析規則。定義在實例建立時載入；錯誤定義會使該任務失敗，不會悄悄捨棄已設定的能力。修改目標清單需要重新啟動伺服器；修改所引用的定義只影響新實例，不改變現有實例。

Creature 使用一般 KT 設定格式。獨立子代理不需要父 Creature，其 YAML/JSON 檔案採用 SubAgentConfig 欄位，使用 `llm` 選擇本機 KT 模型設定，`tools` 支援工具名稱或一般工具設定項目：

```yaml
name: reviewer
llm: default
system_prompt: "Review the requested change. Report concrete findings."
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

自訂工具、套件工具與外掛沿用 KT 現有工廠，相對於定義位置解析。省略 `llm` 時，也可透過 `model` 選擇子代理模型；這裡沒有父模型可繼承。首版不支援互動式子代理。執行限制與沙箱策略應設定在目標定義及其外掛中。

先用一般 KT 命令設定本機模型憑證與模型設定，再儲存並啟用伺服器設定：

```bash
kt mcp-serve setup --config ./mcp.yaml
kt mcp-serve stop
kt mcp-serve start
```

重新啟動後重新整理用戶端工具清單。註冊委派目標後會增加六個工具：

| 工具 | 用途 |
| --- | --- |
| `delegation_targets` | 列出目標別名與描述，不啟動模型 |
| `delegate` | 提交 `target`、`prompt`，可用 `session_id` 延續 Creature 對話 |
| `delegation_send` | 依 KT 現有輸入語意向活動中的 `job_id` 補充資訊 |
| `delegation_sessions` | 列出本伺服器擁有的對話、忙碌狀態與目前委派任務 |
| `delegation_history` | 分頁讀取對話活動或目前公開對話快照 |
| `delegation_close` | 停止並關閉本伺服器擁有的對話，保留可讀取的歷史 |

典型呼叫流程：

1. 呼叫 `delegation_targets`，選擇目標別名。
2. 呼叫 `delegate(target="coder", prompt="Investigate the failing test")`。儲存回傳的兩個 ID：`job_id` 識別本次執行，`session_id` 識別對話。提交會在模型執行前回傳。
3. 使用 `job_status` / `job_wait` 取得結果。每次最多等待 60 秒，等待逾時或用戶端中斷連線都不會取消執行。委派任務已經非同步執行，無須 `job_promote`。
4. 用 `delegation_history(session_id=..., view="events")` 查看活動，或用 `view="conversation"` 查看公開訊息與完整保留的工具結果。透過 `cursor` 與 `limit`（1–200）分頁。活動只保留最新 2,000 筆事件，使用 `truncated` / `earliest_cursor` 報告淘汰情況。對話分頁讀取可變快照，壓縮或進行中的回合可能改變位移量。
5. 用 `delegate(target="coder", session_id=..., prompt="Apply the fix")` 延續對話；省略 `session_id` 則建立獨立對話。
6. 用 `job_cancel` 停止目前委派；不再需要對話時呼叫 `delegation_close`。

不要僅因 HTTP 回覆遺失就重複提交，先查詢任務與對話。同一伺服器的所有已通過驗證的用戶端共享該伺服器的對話與歷史存取權。委派任務紀錄與直接工具任務採用相同的有界保留規則。

### 執行與取消語意

委派實例繼承 MCP 工作區作為工作目錄。不同對話共享該目錄中的檔案，不建立 worktree 或檔案系統隔離。MCP 不額外增加路徑限制；工具、外掛與自動觸發器遵循目標設定，獨立於直接 MCP 工具允許清單及其策略。Creature 使用 KT 無介面 I/O，並保留具名輸出與觸發器。僅註冊目標不會立即建立實例。

每個 Creature 對話同時接受一個活動委派回合。忙碌對話會拒絕新任務，包括正在處理自動觸發回合的情況；此時可能沒有 MCP 委派 job ID。對話清單讀取 Creature 的即時狀態，包括 idle、paused 與 stopped，不根據目標設定推斷狀態。可透過歷史查看其活動。

執行期間的補充輸入不是另一項排隊委派，而是在 KT 正常邊界交付。處理補充輸入時，KT 可能將前景工具轉為背景，隨後原委派回合結束。取消已完成任務不會產生效果；若要停止對話中剩餘的工作，應使用 `delegation_close`。Creature 回合結束不等於其背景任務全部結束；自動活動屬於對話歷史，不會被當作無關任務的結果。

對 Creature 呼叫 `job_cancel` 會重用 KT stop：停止該實例、觸發器及其所有由 KT 管理的工具與子代理，包括先前回合留下的背景工作。取消會等待清理並保留對話歷史。之後明確延續同一個、本伺服器擁有的對話時，會透過 KT 現有持久化與還原流程重建執行環境；取消本身不會自動重新啟動。

取消獨立子代理只停止它自己的任務範圍，並等待原有取消鏈完成。兩種取消操作都不會回復檔案修改，也不承諾回收任意脫離管理的作業系統程序。

Creature 對話在明確關閉或伺服器停止前一直存活；已關閉對話不能延續。子代理是一次性的：執行中接受補充輸入，完成後不能在同一對話繼續，新任務會建立新的子代理。

伺服器不會連接其他 KT 程序、匯入任意已儲存對話，也不會在自身重新啟動後自動還原任務或對話。Creature 持久化使用 `~/.kohakuterrarium/mcp-serve/sessions/<instance-id>/` 下的一般 `.kohakutr` 檔案，MCP 控制代碼仍只在目前伺服器生命週期內有效。

Terrarium 配方不是委派目標。團隊任務的關聯、完成與取消需要獨立的協作協定；內部使用 Terrarium 承載 Creature，並不提供這套團隊層級語意。

## 直接工具的設定細節

`workspace` 必須存在，相對路徑以設定檔所在目錄為基準。省略 `tools` 會啟用前述九個工具。工具名稱必須唯一，`type` 必須為 `builtin`（預設值）。支援 `max_output` 及各工具宣告的執行時選項：`timeout` 適用於 bash/Python，`env` 適用於 bash。不接受逐工具 `working_dir`，因為目錄由共享執行上下文提供。頂層的控制器通知設定、LLM 設定、提示詞、觸發器、compact 與 AgentConfig 繼承會被明確拒絕，不會悄悄忽略。

目錄是預設執行位置，**不是沙箱**。KT 原有的先讀後寫、過期讀取檢查、路徑保護與執行策略仍然適用。`pwd_guard: warn` 會在首次存取目錄外檔案時回傳警告；有意重試會遵循 KT 現有規則。

執行外掛使用一般 `name`、`type`、`module`、`class` 與 `options` 設定。只支援執行側外掛能力：載入與卸載、分派、執行前後鉤子、執行時服務與轉背景。已設定的外掛載入失敗會中止啟動。覆寫 LLM、Agent 生命週期、事件、compact、提示詞、可見性、命令或終止鉤子的外掛會被拒絕。

這些外掛的 PluginContext 提供工作目錄、名稱與實例 ID，但沒有宿主 Agent、Controller、對話持久化、模型切換或子代理建立能力。自訂外掛需要遵守此約定；外掛屬於受信任的本機程式碼。

## 嵌入伺服器

`api.mcp_tools.create_app(config, secret=..., port=..., public_origin=...)` 回傳 ASGI 應用程式。執行其 lifespan，並**關閉宿主存取日誌**。所有請求（包括探索）都需要精確的密鑰路徑。SDK 看到的是已遮蔽的路徑，並檢查允許的 Host/Origin。不要掛載未受保護的副本，也不要同時公開 Studio 管理 API。

## 呼叫、任務與狀態

前景呼叫回傳 Executor job ID、輸出、錯誤、結束碼與中繼資料。原生圖片檔案透過 KT 現有媒體解析器轉換為 MCP 圖片內容。PDF 文字可用；由於此執行環境沒有持久化產物儲存，共享正規化邏輯目前會省略產生的頁面圖片。

對 bash/Python 傳入 `run_in_background: true` 會立即回傳**同一個 KT job ID**，執行繼續進行。四個工具提供現有 JobStore 的存取介面：

| 工具 | 行為 |
| --- | --- |
| `job_status` | 讀取單個任務，或列出保留任務及 `instance_id` |
| `job_wait` | 等待 0–60 秒，預設 10 秒；逾時回傳目前狀態 |
| `job_cancel` | 取消所屬的執行中任務；Creature 委派會停止整個實例 |
| `job_promote` | 將前景呼叫釋放至背景，不重複執行 |

即使任務失敗或被取消，讀取或等待其保留紀錄仍是成功的 MCP 呼叫；`state`、`error` 與 `exit_code` 描述的是任務結果。未知任務、無效查詢參數與前景執行失敗仍屬於 MCP 錯誤，因此用戶端不會將成功的取消狀態查詢誤判為無效呼叫。

不會僅因經過一段時間就自動轉背景。等待請求中斷連線或被取消，不會取消所屬任務；需要取消時使用 `job_cancel`。變更操作的回覆遺失後，先查詢任務再決定是否重試。背景完成**不會**自動喚醒 ChatGPT 對話，用戶端需要主動查詢或等待。

同一伺服器的所有已通過驗證的用戶端共享讀取歷史、工具、外掛與任務；不同實例的狀態互相獨立。正常停止會透過 KT 原有流程取消所屬任務。重新啟動會建立新實例，不還原舊任務，舊 ID 不會匹配新任務。現有 JobStore 最多保留 100 個已完成任務；URL 身分與這些記憶體狀態互相獨立。

## 存取邊界

完整密鑰 URL 是持有者憑證，不是 OAuth 或 ChatGPT 帳號身分；持有 URL 的人即可存取該實例的工具。請避免將其寫入版本控制與一般日誌。HTTPS 通道在服務商處終止 TLS，不應假設服務商無法看到明文。用戶端確認仍由用戶端決定，KT 不會繞過這些確認。

## 參閱

- [MCP 用戶端設定](mcp.md)：將外部 MCP 工具接入 Creature。
- [Creature 設定](creatures.md)：定義本機委派目標。
- [子代理](sub-agents.md)：子代理能力與設定。
- [反向代理部署](deployment-reverse-proxy.md)：維護外部 HTTPS 入口。
