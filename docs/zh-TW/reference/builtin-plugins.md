---
title: 內建外掛
summary: 框架附帶的四個執行期外掛參考 —— sandbox、budget、permgate、compact.auto。
tags:
  - reference
  - plugins
  - builtin
---

# 內建外掛

KohakuTerrarium 附帶四個執行期外掛。它們在框架裡沒有任何特權 —— 用的
是和使用者外掛一樣的 hook。它們在 agent 啟動時被自動發現；config 用
和其他外掛一樣的 `plugins:` 區塊來啟用。

關於框架 / 外掛邊界的設計意圖，以及 sandbox 外掛的逐步示範，請見
[guides/plugins —— 工作範例](../guides/plugins.md#worked-example-why-sandbox-is-a-plugin-not-a-framework-feature)。

| 名稱 | Priority | Hooks | 實作 |
|------|----------|-------|------|
| [`sandbox`](#sandbox) | 1 | `pre_tool_execute`、`runtime_services`、`on_load` | 檔案 / 網路 / 子行程能力閘門。 |
| [`budget`](#budget) | 5 | `pre_llm_call`、`post_llm_call`、`pre_tool_execute`、`pre_subagent_run`、`get_prompt_content` | 回合 / 工具呼叫 / 牆鐘預算記帳。 |
| [`compact.auto`](#compactauto) | 30 | `post_llm_call`、`on_load` | 在高 token LLM 呼叫之後觸發 context 壓縮。 |
| [`permgate`](#permgate) | 100 | `pre_tool_execute`、`on_load` | 互動式使用者批准工具呼叫。 |

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

- **路徑範圍** —— `default` 允許 `cwd` 之下；`workspace` 允許 `cwd` 之
  下並拒絕向上穿越；`broad` 在 `fs_deny` 之外都允許；`deny` 全擋。
- **網路閘門** —— 當 `network=allow` 且設定了 `network_allowlist` 時，
  只有清單上的主機能過；否則在網路允許時全過。當 `network=deny` 時，
  所有網路工具呼叫（`web_fetch`、`web_search`）都會拋
  `PluginBlockError`。
- **子行程閘門** —— 外掛透過 `runtime_services()` 發布
  `subprocess_runner` 服務。需要起子行程的工具（`bash` 等）從
  `ToolContext.runtime_services` 取用。Runner 會先檢查 syscall 等級
  （`pure` 擋所有 spawn、`fs` 擋網路呼叫、`shell` 全允許）與網路白名
  單，然後才委派給 `asyncio.create_subprocess_exec`。
- **`backend=audit`** —— 違規會透過 agent logger 記錄而不拋出。適合
  在新 workload 上做第一輪發現。
- **熱重設** —— 呼叫外掛的 `refresh_options()` 會重建內部能力結構；
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

在 LLM token 用量越過門檻時觸發背景 context 壓縮。
實作：`src/kohakuterrarium/builtins/plugins/compact/auto.py`。

### Options

| Option | 類型 | 預設 | 用途 |
|--------|------|------|------|
| `threshold_ratio` | float | 0.7 | 當 prompt token 用量超過模型 context 視窗的這個比例時觸發。 |
| `min_messages` | int | 8 | 在允許壓縮之前的最小訊息數。 |

### 行為

每一次 LLM 呼叫之後，`post_llm_call` 都會拿 token 用量和門檻比較。如
果超過，就呼叫 `context.compact_manager.trigger_compact()` 排程非同
步壓縮 —— 控制器繼續跑，summariser 在背景工作，切換發生在回合之
間。見
[非阻塞壓縮](../concepts/impl-notes/non-blocking-compaction.md)。

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
| `enabled` | bool | true | 總開關。 |
| `tools` | list[str] / `"all"` | `"all"` | 哪些工具需要批准。`"all"` = 每個工具。 |
| `whitelist` | list[str] | `[]` | 跳過批准的工具（使用者選「always」時也會被加進 per-session 「always allow」清單）。 |
| `auto_approve_pattern` | str | "" | regex 模式；符合的工具 args 會自動批准而不詢問。 |
| `prompt_template` | str | 預設 | 自訂顯示給使用者的批准 prompt。 |

### 行為

`pre_tool_execute` 發出 `tool_approval_request` 輸出事件，附工具名、
args、request id。Agent 在 output bus 上等一個匹配 id 的
`tool_approval_reply` 事件；回覆帶 `decision ∈ {approve, deny,
always}`。`always` 把工具加進 per-session whitelist（持久化在外掛 state
裡）。`deny` 拋 `PluginBlockError`，讓 agent 把它當成普通工具失敗處
理。

網頁 UI 與 TUI 把批准渲染成輸出流上的內聯 prompt；CLI 也透過同一個
output bus 出場，並經由 input loop 回覆。沒有使用者介面的無頭部署應該
關掉 permgate 或設 `auto_approve_pattern: "^.*$"`。

### 範例

```yaml
plugins:
  - name: permgate
    options:
      tools: ["bash", "write", "edit"]
      whitelist: ["read", "grep", "glob"]
```

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
      tools: "all"
  - name: budget
    options:
      turn_budget: [40, 60]
      tool_call_budget: [75, 100]
      walltime_budget: [300, 600]
```

## 另見

- [guides/plugins](../guides/plugins.md) —— 怎麼寫自己的外掛。
- [reference/plugin-hooks](plugin-hooks.md) —— 每個 hook 的 signature。
- [concepts/modules/plugin](../concepts/modules/plugin.md) —— 設計理由。
