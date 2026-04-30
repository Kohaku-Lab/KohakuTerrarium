---
title: CLI
summary: 每一個 kt 子指令 — run、resume、login、install、list、info、model、embedding、search、terrarium、serve、app。
tags:
  - reference
  - cli
---

# CLI 參考

所有 `kt` 指令、子指令、旗標。CLI 是框架給操作者的介面：啟動生物、啟動生態瓶、管理套件、設定 LLM、提供 web UI、搜尋已儲存的工作階段。

生物、生態瓶、root 代理的心智模型請看 [concepts/boundaries](../concepts/boundaries.md)。任務導向的路徑請看 [guides/getting-started](../guides/getting-started.md) 與 [guides/creatures](../guides/creatures.md)。

## 入口

- `kt` — 安裝後的 console script。
- `python -m kohakuterrarium` — 同上。
- 不加子指令執行 (例如從 Briefcase 雙擊) 時，`kt` 會自動開桌面 app。

## 全域旗標

| 旗標 | 用途 |
|---|---|
| `--version` | 印出版本、安裝來源、套件路徑、Python 版本、git commit。 |
| `--verbose` | 配 `--version` 使用；加印 `$VIRTUAL_ENV`、executable、git branch。 |

---

## 核心指令

### `kt run`

跑一隻生物。

```
kt run <agent_path> [flags]
```

Positional：

- `agent_path` — 本地資料夾 (要有 `config.yaml`)，或套件參照，例如 `@kt-biome/creatures/swe`。

旗標：

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `--log-level` | `DEBUG\|INFO\|WARNING\|ERROR` | `INFO` | Root logger 等級。 |
| `--log-stderr` | `auto\|on\|off` | `auto` | 把 log 鏡射到 stderr。`auto` = I/O 模式不是 cli/tui 時開啟 (例如 `plain`、`stdout`、`custom`、`package`)；`off` = 永不；`on` = 永遠開。 |
| `--session` | path | 自動 | 工作階段檔；絕對路徑或放在 `~/.kohakuterrarium/sessions/` 下的名字。 |
| `--no-session` | flag | — | 完全不做持久化。 |
| `--llm` | str | — | 覆寫 LLM profile (例如 `gpt-5.4`、`claude-opus-4.7`)。接受 variation selector — 見 [configuration 參考](configuration.md#variation-selector)。 |
| `--mode` | `cli\|plain\|tui` | 自動 | 互動模式。TTY 上預設 `cli`，非 TTY 預設 `plain`。 |

行為：

- `@package/...` 會解析到 `~/.kohakuterrarium/packages/<pkg>/...`，遇到 `.link` 指標會跟 (給 editable 安裝用)。
- 除非有 `--no-session`，會在 `~/.kohakuterrarium/sessions/` 自動建一個 `.kohakutr` 工作階段。
- 離開時會印 `kt resume <name>` 提示。
- 在 Rich CLI 模式下，Ctrl+C 會中斷目前 turn；閒置時按兩次 Ctrl+C（或 Ctrl+D / `/exit`）會乾淨退出。

### `kt resume`

恢復之前的工作階段。類型 (agent 或 terrarium) 從工作階段檔自動偵測。

```
kt resume [session] [flags]
```

Positional：

- `session` — 名字前綴、完整檔名、或完整路徑。不給就進互動式選單 (顯示最近 10 個)。

旗標：

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `--pwd` | path | 工作階段記錄的 cwd | 覆寫工作目錄。 |
| `--last` | flag | — | 直接 resume 最近一個，不彈選單。 |
| `--log-level` | 同 `kt run` | | |
| `--log-stderr` | 同 `kt run` | `auto` | 把 log 鏡射到 stderr。 |
| `--mode` | 同 `kt run` | | 生態瓶工作階段強制 `tui`。 |
| `--llm` | str | | 覆寫這次 resume 的 LLM profile。支援 variation-selector 簡寫。 |

行為：

- `.kohakutr` 與舊的 `.kt` 副檔名都接受、會自動去掉。
- 前綴比對有歧義時會彈選單。

### `kt list`

列已安裝的套件與本地代理。

```
kt list [--path agents]
```

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `--path` | str | `agents` | 除了已安裝套件之外，另外掃的本地資料夾。主要在 cwd 是一個帶有自家 `agents/` 的專案時有用；已安裝套件不論 `--path` 設什麼都會列出來。 |

### `kt info`

印出生物 config 的名稱、描述、模型、工具、子代理、檔案。

```
kt info <agent_path>
```

---

## Terrarium

### `kt terrarium run`

跑多代理生態瓶。

```
kt terrarium run <terrarium_path> [flags]
```

Positional：

- `terrarium_path` — YAML 檔或 `@package/terrariums/<name>`。

旗標：

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `--log-level` | 同 `kt run` | | |
| `--seed` | str | — | 啟動時注入到 seed 頻道的 prompt。 |
| `--seed-channel` | str | `seed` | 接收 `--seed` 的頻道。 |
| `--observe` | 頻道名字 list | — | 要觀察的頻道 (plain/log 模式)。 |
| `--no-observe` | flag | — | 關掉所有觀察。 |
| `--session` | path | 自動 | 工作階段檔路徑。 |
| `--no-session` | flag | — | 關掉持久化。 |
| `--llm` | str | — | 覆寫**每一隻**生物 (含 root) 的 LLM profile。 |
| `--mode` | `cli\|plain\|tui` | `tui` | UI 模式。 |

行為：

- `tui`：多 tab view — root + 每隻生物 + 每條頻道。
- `cli`：把 root (有的話) 或第一隻生物掛到 RichCLI。
- `plain`：把觀察到的頻道訊息串流到 stdout。

### `kt terrarium info`

印出生態瓶名稱、生物清單、listen/send 頻道、頻道清單。

```
kt terrarium info <terrarium_path>
```

---

## 套件

### `kt install`

從 git URL 或本地路徑安裝套件。

```
kt install <source> [-e|--editable] [--name <name>]
```

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `-e`、`--editable` | flag | — | 寫一個 `<name>.link` 指向來源，而不是複製內容。 |
| `--name` | str | 從 URL/路徑推得 | 覆寫安裝的套件名稱。 |

`<source>` 可以是：

- Git URL (clone 到 `~/.kohakuterrarium/packages/<name>`)。
- 本地資料夾 (複製進去；若加 `-e` 則 link)。

### `kt uninstall`

移除已安裝的套件。

```
kt uninstall <name>
```

### `kt update`

更新 git 來源的套件。會跳過 editable 與非 git 的套件。

```
kt update [target] [--all]
```

| 旗標 | 型別 | 說明 |
|---|---|---|
| `--all` | flag | 更新每一個 git 來源的套件。 |

### `kt edit`

用 `$EDITOR` (沒有則 `$VISUAL`、最後 `nano`) 打開生物或生態瓶 config。

```
kt edit <target>
```

`target` 接受套件參照 (`@pkg/creatures/name`) 與本地路徑。

---

## 設定：`kt config`

### `kt config show`

印出 CLI 用到的每個 config 檔路徑。

### `kt config path`

印出某個 config 的路徑，對象可以是：`home`、`llm_profiles`、`api_keys`、`mcp_servers`、`ui_prefs`。

```
kt config path [name]
```

### `kt config edit`

用 `$EDITOR` 打開 config 檔。不給名字預設打開 `llm_profiles`。

```
kt config edit [name]
```

### `kt config provider` (別名：`kt config backend`)

管理 LLM provider (backend)。

#### `kt config provider list`

顯示每個 provider 的名稱、backend 類型、Base URL。

#### `kt config provider add`

互動式。會問 backend type、base URL、`api_key_env`、provider identity，以及要暴露的 provider-native tools。選單提供 `openai`、`anthropic` 與 `codex`；儲存時也使用這些標準 backend type 值。

```
kt config provider add [name]
```

`kt config provider add` 跟 `kt config provider edit` 走同一條互動路徑；差別只在 `edit` 要求傳 positional name 且會預填現有值。

#### `kt config provider edit`

欄位同 `add`，預填現有值。

```
kt config provider edit <name>
```

#### `kt config provider delete`

```
kt config provider delete <name>
```

### `kt config llm` (別名：`kt config model`、`kt config preset`)

管理 LLM preset。

#### `kt config llm list`

顯示 Name、Provider、Model、Groups 欄位 (variation group 名稱，用逗號分隔；沒有就空)、Default 標記。預設只列使用者定義的 preset。

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `--all` | flag | — | 一併列出所有內建 preset。列會分組 (使用者 preset / 內建 preset)；Legend 會標出哪些條目已有 API key / OAuth 設定。 |

#### `kt config llm show`

印出完整 preset：name、provider、backend type、model、`max_context` / `max_output`、base URL、`api_key_env`、temperature、reasoning effort、service tier、目前的 variation selection (若有)、preset 宣告的 variation group 與對應 selector 範例 (例如 `claude-opus-4.7@reasoning=xhigh`)，以及 `extra_body`。

```
kt config llm show <name>
```

#### `kt config llm add`

互動式。會問是否把新 preset 設成預設 (預設 No)。

```
kt config llm add [name]
```

#### `kt config llm edit`

```
kt config llm edit <name>
```

#### `kt config llm delete`

```
kt config llm delete <name>
```

#### `kt config llm default`

不給引數時印出目前預設。給 `name` 就設成預設。

```
kt config llm default [name]
```

### `kt config key`

管理存起來的 API key。

#### `kt config key list`

欄位：provider、api_key_env、來源 (`stored`/`env`/`missing`)、遮罩過的值。

#### `kt config key set`

把 API key 存到 `~/.kohakuterrarium/api_keys.yaml`。沒給 `value` 時會遮罩提示輸入。

```
kt config key set <provider> [value]
```

#### `kt config key delete`

清除存起來的 key (provider 的條目本身保留)。

```
kt config key delete <provider>
```

### `kt config login`

`kt login` 的別名。見 [Auth](#auth)。

### `kt config mcp`

管理全域 MCP server 目錄 (`~/.kohakuterrarium/mcp_servers.yaml`)。

- `list` — 顯示檔案路徑與 server 清單。
- `add [name]` — 互動式。會問 transport (`stdio`/`http`)、command、args JSON、env JSON、URL。
- `edit <name>` — 互動式編輯。
- `delete <name>` — 移除條目。

---

## Auth

### `kt login`

對某個 provider 做驗證。

```
kt login <provider>
```

- `codex` backend：OAuth device-code 流程。Token 存在 `~/.kohakuterrarium/codex-auth.json`。
- API-key backend：遮罩提示輸入，存到 `~/.kohakuterrarium/api_keys.yaml`。

---

## Model

### `kt model`

`kt config llm` 的薄包裝、為了向後相容保留。新文件建議用 `kt config llm`；`kt model` 以 one-liner 別名繼續存在。

```
kt model list
kt model default [name]
kt model show <name>
```

---

## 記憶與搜尋

### `kt embedding`

為已存的工作階段建 FTS 與向量索引。`<session>` 接受名字前綴、完整檔名或路徑；除了 `.kohakutr` 也認舊的 `.kt` 副檔名。

```
kt embedding <session> [--provider ...] [--model ...] [--dimensions N]
```

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `--provider` | `auto\|model2vec\|sentence-transformer\|api` | `auto` | Auto 優先用 jina-v5-nano。 |
| `--model` | str | 視 provider 而定 | 該 provider 對應的 model，含別名如 `@tiny`、`@best`、`@multilingual-best`。 |
| `--dimensions` | int | — | Matryoshka 截斷 (較短的向量)。 |

### `kt search`

搜尋工作階段的記憶。`<session>` 接受的形式跟 `kt embedding` 相同 (舊的 `.kt` 也認)。

```
kt search <session> <query> [flags]
```

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `--mode` | `fts\|semantic\|hybrid\|auto` | `auto` | 搜尋模式。Auto 有向量就走 semantic，否則走 FTS。 |
| `--agent` | str | — | 只看某隻代理的事件。 |
| `-k` | int | `10` | 最多回傳幾筆。 |

---

## Web 與桌面 UI

### `kt web`

跑 web server (阻塞、單一程序)。

```
kt web [flags]
```

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `--host` | str | `127.0.0.1` | 綁定 host。 |
| `--port` | int | `8001` | 綁定 port。被佔用會自動遞增。 |
| `--dev` | flag | — | 只起 API (前端自己用 `vite dev` 起)。 |
| `--log-level` | 同 `kt run` | | |

### `kt app`

跑原生桌面版本 (需要 pywebview)。

```
kt app [--port 8001] [--log-level ...]
```

### `kt serve`

Web server 的 daemon 管理。程序狀態存在 `~/.kohakuterrarium/run/web.{pid,json,log}`。

#### `kt serve start`

以 detached 方式啟動 server 程序。

```
kt serve start [--host 127.0.0.1] [--port 8001] [--dev] [--log-level INFO]
```

#### `kt serve stop`

先 SIGTERM，過 grace period 後 SIGKILL。

```
kt serve stop [--timeout 5.0]
```

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `--timeout` | float | `5.0` | 等待 graceful shutdown 的秒數。 |

#### `kt serve restart`

先 `stop` 再 `start`，把所有旗標轉給 `start`。

#### `kt serve status`

印出 `running` / `stopped` / `stale`、PID、URL、started_at、版本、git commit。

#### `kt serve logs`

讀 `~/.kohakuterrarium/run/web.log`。

```
kt serve logs [--follow] [--lines 80]
```

| 旗標 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `--follow` | flag | — | Tail log。 |
| `--lines` | int | `80` | 一開始印的行數。 |

---

## Extension

### `kt extension list`

列已安裝套件提供的所有工具、外掛、LLM preset。Editable 安裝會標註。

### `kt extension info`

顯示套件 metadata，加上它的生物、生態瓶、工具、外掛、LLM preset。

```
kt extension info <name>
```

---

## MCP (每隻代理)

### `kt mcp list`

列某隻代理 `config.yaml` 的 `mcp_servers:` 區段裡宣告的 MCP server。欄位：name、transport、command、URL、args、env key。

```
kt mcp list --agent <path>
```

MCP server 也可以放在 `~/.kohakuterrarium/mcp_servers.yaml` 的全域目錄裡，由 [`kt config mcp`](#kt-config-mcp) 管理。兩邊的 registry 是獨立的 — 每隻代理的條目會在代理啟動時連上；目錄裡的條目不會自動連，但可以用名字引用。

---

## 檔案路徑

| 路徑 | 用途 |
|---|---|
| `~/.kohakuterrarium/` | Home。 |
| `~/.kohakuterrarium/llm_profiles.yaml` | LLM preset 與 provider。 |
| `~/.kohakuterrarium/api_keys.yaml` | 儲存的 API key。 |
| `~/.kohakuterrarium/mcp_servers.yaml` | 全域 MCP server 目錄。 |
| `~/.kohakuterrarium/ui_prefs.json` | UI 偏好設定。 |
| `~/.kohakuterrarium/codex-auth.json` | Codex OAuth token。 |
| `~/.kohakuterrarium/sessions/*.kohakutr` | 存起來的工作階段 (舊的 `*.kt` 也接受)。 |
| `~/.kohakuterrarium/packages/` | 已安裝套件 (複製或 `.link` 指標)。 |
| `~/.kohakuterrarium/run/web.{pid,json,log}` | Web daemon 狀態。 |

## 環境變數

| 變數 | 用途 |
|---|---|
| `EDITOR`、`VISUAL` | `kt edit` / `kt config edit` 用的編輯器。 |
| `VIRTUAL_ENV` | `kt --version --verbose` 會印出。 |
| `<PROVIDER>_API_KEY` | 每個 provider 的 `api_key_env` 指到的變數。 |
| `KT_SHELL_PATH` | 覆寫 `bash` 工具用的 shell。 |
| `KT_SESSION_DIR` | 覆寫 web API 的工作階段目錄 (預設 `~/.kohakuterrarium/sessions`)。 |

## Exit code

- `0` — 成功。
- `1` — 一般錯誤。
- 編輯器的 exit code — 用於 `kt edit` / `kt config edit`。

## 互動式 prompt

這些指令可能會進入互動式 prompt：

- `kt resume` 沒給引數或前綴有歧義。
- `kt terrarium run` 沒 root 也沒 `--seed`。
- `kt login`。
- `kt config` 底下所有 `... add` 子指令。
- `kt config key set` 沒給值。

## 套件參照語法

`@<pkg-name>/<path-inside-pkg>` 會解析到 `~/.kohakuterrarium/packages/<pkg-name>/<path-inside-pkg>`，或跟 `<pkg-name>.link` 走。`kt run`、`kt terrarium run`、`kt edit`、`kt update`、`kt info` 都接受。

## Terrarium TUI slash 指令

在 `kt terrarium run --mode tui` 裡，輸入列接受 slash 指令。內建：`/exit`、`/quit`。其他指令來自生態瓶註冊的 user command。見 [builtins.md#user-commands](builtins.md#user-commands)。

## 延伸閱讀

- 概念：[邊界](../concepts/boundaries.md)、[工作階段持久化](../concepts/impl-notes/session-persistence.md)。
- 指南：[快速開始](../guides/getting-started.md)、[工作階段](../guides/sessions.md)、[生態瓶](../guides/terrariums.md)。
- 參考：[設定](configuration.md)、[內建模組](builtins.md)、[HTTP](http.md)。
