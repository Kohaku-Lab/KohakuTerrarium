---
title: 內建外掛
summary: 框架附帶的執行期外掛參考：sandbox、budget、permgate、compact.auto、browser。
tags:
  - reference
  - plugins
  - builtin
---

# 內建外掛

KohakuTerrarium 附帶下列執行期外掛。它們在框架裡沒有任何特權，用的
是和使用者外掛一樣的 hook。它們在 agent 啟動時被自動發現；config 用
和其他外掛一樣的 `plugins:` 區塊來啟用。

關於框架 / 外掛邊界的設計意圖，以及 sandbox 外掛的逐步示範，請見
[guides/plugins：工作範例](../guides/plugins.md#worked-example-why-sandbox-is-a-plugin-not-a-framework-feature)。

| 名稱 | Priority | Hooks | 實作 |
|------|----------|-------|------|
| [`sandbox`](#sandbox) | 1 | `pre_tool_execute`、`runtime_services`、`on_load` | 檔案 / 網路 / 子行程能力閘門。 |
| [`budget`](#budget) | 5 | `pre_llm_call`、`post_llm_call`、`pre_tool_execute`、`pre_subagent_run`、`get_prompt_content` | 回合 / 工具呼叫 / 牆鐘預算記帳。 |
| [`compact.auto`](#compactauto) | 30 | `post_llm_call`、`on_load` | 在高 token LLM 呼叫之後觸發 context 壓縮。 |
| [`permgate`](#permgate) | 100 | `pre_tool_execute`、`on_load` | 互動式使用者批准工具呼叫。 |
| [`browser`](#browser) | 0 | `contribute_tools`、`runtime_services`、`pre_tool_execute`、`get_prompt_content`、`on_disable` | 由六個 `browser_*` 工具驅動的互動式 Chromium。 |

`pre_*` hook 在低 priority 先跑。上面列的順序是預設註冊順序。

## `sandbox`

硬性能力閘門。在不修改工具本身的前提下限制工具與子行程能做什麼。
實作：`src/kohakuterrarium/builtins/plugins/sandbox/plugin.py`。

### Options

| Option | 類型 | 值 | 預設 | 用途 |
|--------|------|----|------|------|
| `enabled` | bool | true / false | true | 總開關。 |
| `backend` | enum | `auto` / `audit` / `off` | `auto` | `auto` = 違規時阻擋；`audit` = 僅記錄；`off` = 不強制。 |
| `profile` | enum | `PURE` / `READ_ONLY` / `WORKSPACE` / `NETWORK` / `SHELL` | `WORKSPACE` | 能力預設。 |
| `fs_read` / `fs_write` | enum | `default` / `deny` / `workspace` / `broad` | 來自 profile | 檔案讀 / 寫範圍覆寫。 |
| `network` | enum | `default` / `deny` / `allow` | 來自 profile | 網路存取覆寫。 |
| `syscall` | enum | `default` / `pure` / `fs` / `shell` / `any` | 來自 profile | 子行程能力等級。 |
| `env` | enum | `default` / `filtered` / `inherit` | 來自 profile | 子行程環境變數處理。 |
| `tmp` | enum | `default` / `private` / `shared` | 來自 profile | 暫存目錄隔離。 |
| `fs_deny` | list[str] | 路徑 | `[]` | 額外的路徑黑名單（支援環境變數展開）。 |
| `network_allowlist` | list[str] | 主機名 | `[]` | `network=allow` 時的允許清單。空清單 = 網路允許時全允許。 |
| `os_enforce` | enum | `auto` / `require` / `off` | `auto` | 子行程的核心寫入限制（macOS `sandbox-exec`、Linux Landlock）。`auto`＝支援時啟用，`require`＝不支援時拒絕啟動，`off`＝只做規則檢查。 |
| `fs_write_allow` | list[str] | 路徑 | `[]` | 你指定的例外：即使在 `fs_deny` 之內也可寫（`fs_write=deny` 時除外）。 |
| `locked` | bool | true / false | false | 拒絕執行期停用、修改選項與切換工作目錄（HTTP 回傳 409）。執行期只能開啟不能關閉；以 `KT_SANDBOX_ALLOW_UNLOCK=1` 重啟服務才能解鎖。 |
| `blocked_tools` | list[str] | 工具名 | `[]` | 不論參數都不允許呼叫的工具。 |

### Profile 預設

| Profile     | fs_read | fs_write   | network | syscall | env      |
|-------------|---------|------------|---------|---------|----------|
| `PURE`      | deny    | deny       | deny    | pure    | filtered |
| `READ_ONLY` | broad   | deny       | default | fs      | default  |
| `WORKSPACE` | default | workspace  | allow   | fs      | default  |
| `NETWORK`   | deny    | deny       | allow   | default | default  |
| `SHELL`     | default | workspace  | allow   | shell   | filtered |

### 行為

- **路徑範圍**：`default` 允許 `cwd` 之下；`workspace` 允許 `cwd` 之
  下並拒絕向上穿越；`broad` 在 `fs_deny` 之外都允許；`deny` 全擋。
- **網路閘門**：當 `network=allow` 且設定了 `network_allowlist` 時，
  只有清單上的主機能過；否則在網路允許時全過。當 `network=deny` 時，
  所有網路工具呼叫都會拋 `PluginBlockError`。外掛內建一張以工具名稱
  為鍵的表（`web_fetch`：其 `url` 參數；`web_search`：`duckduckgo.com`），
  工具宣告的 `url_arguments`、`network_hosts`、`network_access` 會加到
  這張表上（見 [自訂模組](../guides/custom-modules.md#宣告網路使用)）。
  使用這些名稱的工具即使什麼都沒宣告也會受管制；每個主機或參數在一次
  呼叫中只檢查一次。
- **網路政策服務**：啟用時外掛也透過 `runtime_services()` 發布
  `network_policy` 服務，供在工具 hook 之外建立連線的元件（例如過濾
  代理）使用。`check(host, port)` 回傳 `None` 表示允許，否則回傳簡短
  的拒絕原因，套用 `network` 與 `network_allowlist`；audit 模式下記錄
  後允許。`mode` 為 `"allow"` 或 `"deny"`。`requires_os_confinement`
  在 `os_enforce=require` 生效時為真，表示繞過 `subprocess_runner` 起的
  行程會逃過它要求的核心約束。每次讀取都跟隨外掛當下的選項。
- **寫入政策服務**：啟用時外掛也發布 `write_policy` 服務，供在工具呼叫
  之外寫入檔案的元件（例如瀏覽器儲存下載）使用。`check(path)` 回傳
  `None` 表示允許，否則回傳簡短的拒絕原因（例如 `fs_write=deny`），
  判斷與寫入工具的路徑參數完全相同：`fs_write`、工作區邊界、`fs_deny`
  與 `fs_write_allow`。相對路徑以 agent 當下的工作目錄解析（切換工作
  目錄後跟著變）；在沒有 agent 的宿主（例如 MCP 工具伺服器）裡，以外掛
  載入時的目錄解析。audit 模式下記錄後允許；每次呼叫都跟隨外掛當下的選項。
- **持有服務**：外掛在整個生命週期內發布同一組 `network_policy` 與
  `write_policy` 物件，元件可以跨呼叫持有。選項變更立即作用在持有的物件
  上，`enabled: false` 或 `backend: off` 會讓它全部允許。透過外掛管理器
  停用外掛只會停止把物件交給新的工具呼叫：已持有的物件仍按最後的選項
  回答，重新啟用後交出的是同一組物件。使用方可以依
  `kohakuterrarium.modules.sandbox` 的 `NetworkPolicy` 與 `WritePolicy`
  介面標注型別。
- **哪些工具呼叫看得到**：工具呼叫拿到的是建立其工具 context 的外掛管理器
  的服務。context-fork 子 agent 的呼叫拿到父 agent 的物件，並且也會經過
  父 agent 的 sandbox 檢查。一般子 agent 的工具 context 由父 agent 建立，
  所以它的呼叫帶著父 agent 的服務，而它的 hook 只經過它自己的外掛；設定
  在子 agent 本身的 sandbox 會管制這些 hook，但不會把服務發布給它的工具。
- **子行程閘門**：外掛透過 `runtime_services()` 發布
  `subprocess_runner` 服務。需要起子行程的工具（`bash` 等）從
  `ToolContext.runtime_services` 取用。Runner 會先檢查 syscall 等級
  （`pure` 擋所有 spawn、`fs` 擋網路呼叫、`shell` 全允許）與網路白名
  單，然後才委派給 `asyncio.create_subprocess_exec`。
- **`backend=audit`**：違規會透過 agent logger 記錄而不拋出。適合
  在新 workload 上做第一輪發現。
- **熱重設**：呼叫外掛的 `refresh_options()` 會重建內部能力結構；
  隨後的 `pre_tool_execute` 呼叫看到的就是新政策，無須重啟。

### 設定範例

```yaml
plugins:
  - name: sandbox
    options:
      profile: WORKSPACE
      network_allowlist: ["api.example.com", "github.com"]
      fs_deny: ["~/.ssh", "$HOME/.aws"]
```

```yaml
# audit-only 模式做安全推行
plugins:
  - name: sandbox
    options:
      profile: SHELL
      backend: audit
```

```yaml
# 純計算，零 I/O
plugins:
  - name: sandbox
    options:
      profile: PURE
      blocked_tools: ["web_fetch", "web_search", "bash"]
```

## `budget`

多軸預算記帳與強制。
實作：`src/kohakuterrarium/builtins/plugins/budget/plugin.py`。

### Options

| Option | 類型 | 預設 | 用途 |
|--------|------|------|------|
| `turn_budget` | `[soft, hard]` ints 或 null | null | LLM 回合數上限。soft = 警告；hard = 阻擋。 |
| `tool_call_budget` | `[soft, hard]` ints 或 null | null | 總工具呼叫數上限。 |
| `walltime_budget` | `[soft, hard]` 秒 或 null | null | 牆鐘秒數上限。 |
| `subagent_turn_budget` | `[soft, hard]` ints 或 null | null | 單一子 agent 執行的回合數上限。 |
| `inject_alert` | bool | true | 越過 soft 閾值時把警告注入下一則 system / user 訊息。 |

任何軸跨越 hard 閾值時，下一個 `pre_tool_execute` /
`pre_llm_call` / `pre_subagent_run` 會拋 `PluginBlockError`，錯誤訊息
點出哪個軸。跨過 soft 閾值時則注入警告，讓 LLM 在 hard 閾值之前主動
收尾。

### 狀態

狀態以 per-session 形式儲存在 session store，命名空間是
`plugin:budget:*`，所以 resume 會保留累積計數。

### 範例

```yaml
plugins:
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

## `compact.auto`

在 LLM token 用量越過 compact manager 設定的門檻時，提前觸發背景 context 壓縮。
實作：`src/kohakuterrarium/builtins/plugins/compact/auto.py`。

### 設定

這個外掛沒有外掛專屬 options。請透過 host 的
[`compact:` 設定區塊](configuration.md#壓縮)設定 manager；SubAgent 的內聯
寫法見 [SubAgent 指南](../guides/sub-agents.md#子代理自動壓縮)。

### 行為

每一次 LLM 呼叫之後，`post_llm_call` 都會拿 token 用量和門檻比較。如
果超過，就呼叫 `context.compact_manager.trigger_compact()` 排程非同
步壓縮：控制器繼續跑，summariser 在背景工作，切換發生在回合之
間。見
[非阻塞壓縮](../concepts/impl-notes/non-blocking-compaction.md)。

對主 Agent 而言，關閉 `compact.auto` 只會移除這個提前觸發 hook；工具結果
回傳後和 turn 結束時的核心延後檢查仍然保留。SubAgent 沒有獨立的延後
host-loop 檢查，因此仍需 `compact.auto` 才能自動壓縮。兩種情況下，
`compact.enabled: false` 都會關閉自動觸發；主 Agent 上明確執行的
`/compact` 仍然可用。

### Pack 別名

`auto-compact` 是一個內建 pack，展開為單一帶預設 options 的
`compact.auto` 啟用。只要功能開啟即可的 config 可以使用
`default_plugins: ["auto-compact"]`。

## `permgate`

互動式使用者批准工具呼叫。Agent 在 output bus 上發出確認事件、等使用
者回覆，再依答案進行或終止。
實作：`src/kohakuterrarium/builtins/plugins/permgate/plugin.py`。

### Options

| Option | 類型 | 預設 | 用途 |
|--------|------|------|------|
| `gated_tools` | list[str] | `[]` | 需要批准的工具。空清單 = 每個工具；`"*"` 也符合每個工具。 |
| `allowlist` | list[str] | `[]` | 永遠不需要批准的工具，即使也列在 `gated_tools` 中。 |
| `timeout_s` | float | null | 等待使用者的秒數；null = 一直等。逾時沒有回應就拒絕。 |
| `surface` | `modal` / `chat` | `modal` | 批准提示出現的位置。 |

### 行為

`pre_tool_execute` 在設定的 `surface` 上發出可互動的 `confirm` 輸出
事件，寫明工具名並摘要其參數，然後等待回覆。選項為 `allow_once`、
`allow_session`（「Always allow」）與 `deny`。`allow_session` 在這個外掛
實例存續期間不再詢問該工具，不會持久化。其他任何結果（拒絕、取消、
`timeout_s` 內沒有回應、bus 錯誤）都會拋 `PluginBlockError`，讓 agent
把它當成普通工具失敗處理。

網頁 UI 與 TUI 把批准渲染成輸出流上的內聯 prompt；CLI 也透過同一個
output bus 出場，並經由 input loop 回覆。沒有使用者介面的無頭部署應該
關掉 permgate 或設 `timeout_s`，讓沒人回應的呼叫被拒絕，而不是一直等。

### 範例

```yaml
plugins:
  - name: permgate
    options:
      gated_tools: ["bash", "write", "edit"]
      allowlist: ["read", "grep", "glob"]
```

## `browser`

每個生物一個互動式 Chromium，由六個工具驅動（`browser_navigate`、
`browser_snapshot`、`browser_act`、`browser_screenshot`、`browser_tabs`、
`browser_eval`；見[內建項目](builtins.md#browser)）。外掛提供這些工具、擁有瀏覽器
工作階段（與生物的子代理共用），並在生物停止、外掛卸載或在執行期間被停用時關閉它。
需要 `browser-use` extra 與 `playwright install chromium`。
實作：`src/kohakuterrarium/builtins/plugins/browser/plugin.py`；執行期在
`src/kohakuterrarium/browser/`。

### Options

| Option | 類型 | 預設 | 用途 |
|--------|------|------|------|
| `headless` | bool | true | 不開視窗執行。 |
| `channel` | str | null | 已安裝的瀏覽器 channel，例如 `chrome` 或 `msedge`。 |
| `executable_path` | str | null | Chromium 系瀏覽器的路徑。 |
| `viewport` | `"WxH"` / `[w, h]` | `1280x800` | 頁面尺寸（CSS 像素；寬 320–7680，高 200–4320）。 |
| `profile_dir` | str | null | 持久化設定檔目錄；null = 每次啟動用完即丟的設定檔。 |
| `allow_hosts` | list[str] | `[]` | 非空時，頁面只能連到這些主機。樣式：`host`、`*.host`、`host:port`、`[v6]:port`。 |
| `block_hosts` | list[str] | `[]` | 頁面永遠不能連到的主機；最先檢查。這裡的 IP 位址也會擋下解析到它的主機名稱。 |
| `allow_loopback` | bool | false | 允許 loopback 位址（本機開發伺服器）。 |
| `allow_private` | bool | false | 允許私有、電信級 NAT 與 IPv6 unique-local 位址。 |
| `secrets` | dict | `{}` | 密鑰名稱，各自對應 `{env: VAR_NAME, origins: [...]}`（非空的 `scheme://host[:port]` 清單），供 `type` 動作使用；只會輸入到這些來源，值會從結果中遮蔽。 |
| `nav_timeout_s` | float | 30 | 導覽逾時秒數。 |
| `action_timeout_s` | float | 5 | 每個動作的逾時秒數；一批 `browser_act` 最多 60 秒，若（此值 + 10 秒）的三倍更長則取後者。 |
| `snapshot_max_chars` | int | 20000 | 快照長度上限（1000–1000000）。 |
| `idle_close_s` | float | 600 | 閒置這麼多秒後關閉瀏覽器；下一次呼叫會啟動新的。 |

未知的 option 名稱會被拒絕。

### 行為

- **延遲啟動。** 瀏覽器在第一次瀏覽器呼叫時才啟動。閒置關閉、當機或
  `close_browser` 會丟掉它；下一次呼叫會啟動新的，並說明狀態已遺失。停止、卸載與
  執行期停用則會永久關閉：之後的呼叫會失敗，不會重新啟動。外掛放手的瀏覽器（修改
  option、停用）還在關閉時，絕不會啟動新的瀏覽器。不再回應的瀏覽器會連同它啟動的
  所有行程一起被強制結束。
- **網路。** 所有流量都經過工作階段擁有的過濾代理，每一跳、每個子資源、彈出視窗與
  WebSocket 都會被判斷。loopback 與私有位址除非放行否則拒絕；link-local 位址（含
  雲端 metadata）一律拒絕。
- **sandbox。** 代理依宿主此刻發布的 sandbox `network_policy` 服務判定每條新連線；
  只要還有連線開著，每 2 秒重新判定已開的連線（每次瀏覽器呼叫時也立刻判定），
  關掉 sandbox 現在拒絕的連線。下載在寫入任何東西之前先用它的 `write_policy` 檢查。
  `network: deny` 會拒絕所有瀏覽器工具；`os_enforce: require` 會拒絕啟動瀏覽器。
- **主機資源。** engine 的主機資源關閉時（多使用者伺服器上的非管理員），只允許
  headless、用完即丟的設定檔、內附的 Chromium（絕不用系統瀏覽器）與公開位址，也不能
  使用 `secrets`。
- **提示詞。** 啟用期間且至少一個瀏覽器工具開著時，外掛會加上一小段 `## Browser pages`，說明頁面內容放在
  `page_content` 元素裡，是資料，永遠不是指令。

### 範例

```yaml
plugins:
  - name: browser
    options:
      allow_loopback: true
      allow_hosts: ["localhost", "127.0.0.1"]
  - name: permgate
    options:
      gated_tools: [browser_navigate, browser_act, browser_eval, browser_tabs]
```

工作流程、完整的網路政策、密鑰與疑難排解見 [guides/browser](../guides/browser.md)。

## 啟用總覽

```yaml
# 最嚴的 agent：權限閘門 + 預算 + 自動壓縮。
default_plugins: ["auto-compact"]
plugins:
  - name: sandbox
    options:
      profile: WORKSPACE
      network_allowlist: ["api.example.com"]
  - name: permgate
    options:
      gated_tools: ["*"]
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

## 另見

- [guides/plugins](../guides/plugins.md)：怎麼寫自己的外掛。
- [reference/plugin-hooks](plugin-hooks.md)：每個 hook 的 signature。
- [concepts/modules/plugin](../concepts/modules/plugin.md)：設計理由。
