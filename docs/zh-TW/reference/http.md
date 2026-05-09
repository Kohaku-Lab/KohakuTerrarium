---
title: HTTP API
summary: kt serve 的 REST 端點與 WebSocket 通道，含 request / response 結構。
tags:
  - reference
  - http
  - api
---

# HTTP 與 WebSocket API

套件內建 FastAPI server (`kt web`、`kt serve`、`python -m kohakuterrarium.api.main`) 暴露的所有 REST 端點與 WebSocket 通道。這個 API 是 Vue SPA 的後端，任何想從程序外控制代理與生態瓶的 client 都可以用它。

Serving 層與工作階段儲存的結構請看 [concepts/impl-notes/session-persistence](../concepts/impl-notes/session-persistence.md)。任務導向的用法請看 [guides/programmatic-usage](../guides/programmatic-usage.md) 與 [guides/frontend-layout](../guides/frontend-layout.md)。

## Server 設定

- 預設 host：`0.0.0.0`。
- 預設 port：`8001` (`kt web` 底下被佔用會自動遞增)。
- 覆寫：`python -m kohakuterrarium.api.main --host 127.0.0.1 --port 8080 [--reload]`。
- `KT_SESSION_DIR` 覆寫預設工作階段目錄。
- CORS 全開：`allow_origins=["*"]`、所有方法、所有 header。
- 沒有驗證。把這個 server 當成被信任的本機服務處理。
- 版本字串：`0.1.0`。沒有 `/v1/` URL prefix。
- FastAPI auto-docs：`/docs` (Swagger UI)、`/redoc` (ReDoc)。

當 `create_app(static_dir=Path)` 收到有效的 SPA build 目錄時：

- `/assets/*` — 帶 hash 的 build 資產。
- `/{path}` — SPA fallback，對任何未比對的路徑送 `index.html`。
- `/api/*` 與 WebSocket 路由優先。

## Response 慣例

- 狀態碼：`200` 成功、`400` 輸入錯誤、`404` 資源不存在、`500` server error。不用 `201`。
- Payload 除非另註明，都是 JSON。
- 錯誤用 FastAPI `HTTPException`：`{"detail": "<message>"}`。

---

## Terrarium

### `POST /api/terrariums`

從 config 路徑建一個生態瓶並啟動。

- Body：`TerrariumCreate` (`config_path`、選用 `llm`、`pwd`)。
- Response：`{"terrarium_id": str, "status": "running"}`。
- 狀態：`200`、`400`。
- Side effect：生出生態瓶、初始化特權 `root:` 節點、啟動生物、設了就開 session store。

### `GET /api/terrariums`

列出所有執行中的生態瓶，回一個狀態物件 array (形狀同下面的單一 terrarium GET)。

### `GET /api/terrariums/{terrarium_id}`

回 `TerrariumStatus`：`terrarium_id`、`name`、`running`、`creatures` (name → status dict)、`channels` (頻道名稱清單)。

### `DELETE /api/terrariums/{terrarium_id}`

停下並清理生態瓶。Response：`{"status": "stopped"}`。Side effect：所有生物停掉、頻道清掉、session store 關閉。

### `POST /api/terrariums/{terrarium_id}/channels`

執行期加一條頻道。

- Body：`ChannelAdd` (`name`、`channel_type` 預設 `"queue"`、`description`)。
- Response：`{"status": "created", "channel": <name>}`。

### `GET /api/terrariums/{terrarium_id}/channels`

列頻道：`[{"name", "type", "description"}]`。

### `POST /api/terrariums/{terrarium_id}/channels/{channel_name}/send`

往頻道塞一則訊息。

- Body：`ChannelSend` (`content` 可以是 `str` 或 `list[ContentPartPayload]`、`sender` 預設 `"human"`)。
- Response：`{"message_id": str, "status": "sent"}`。
- Side effect：訊息寫入歷程、listener 觸發 `on_send` callback。

### `POST /api/terrariums/{terrarium_id}/chat/{target}`

非串流 chat。`target` 可以是 `"root"` 或生物名稱。

- Body：`AgentChat` (`message` 或 `content`)。
- Response：`{"response": <完整文字>}`。

### `GET /api/terrariums/{terrarium_id}/history/{target}`

讀對話與事件日誌。`target` 是 `"root"`、生物名稱、或 `"ch:<channel_name>"` (頻道歷程)。優先用 SessionStore，失敗就 fallback 到記憶體 log。

- Response：`{"terrarium_id", "target", "messages": [...], "events": [...], "is_processing": bool}`。頻道歷程 target 會回傳 `messages`，且 `is_processing` 為 `false`。

### `GET /api/terrariums/{terrarium_id}/scratchpad/{target}`

回目標代理的草稿區，形式是 `{key: value}`。

### `PATCH /api/terrariums/{terrarium_id}/scratchpad/{target}`

- Body：`ScratchpadPatch` (`updates: {key: value | null}`；`null` 表示刪除)。
- Response：更新後的草稿區。

### `GET /api/terrariums/{terrarium_id}/triggers/{target}`

列出活著的 remote trigger：`[{"trigger_id", "trigger_type", "running", "created_at"}]`。

### `GET /api/terrariums/{terrarium_id}/plugins/{target}`

列出已載入的外掛與啟用狀態。

### `POST /api/terrariums/{terrarium_id}/plugins/{target}/{plugin_name}/toggle`

切換外掛啟用狀態。Response：`{"name", "enabled"}`。啟用時會呼叫 `load_pending()`。

### `GET /api/terrariums/{terrarium_id}/env/{target}`

回 `{"pwd", "env"}`；env 裡含有 `secret`、`key`、`token`、`password`、`pass`、`private`、`auth`、`credential` 等字樣 (不分大小寫) 的 key 會被濾掉。

### `GET /api/terrariums/{terrarium_id}/system-prompt/{target}`

回 `{"text": <組裝後的 system prompt>}`。

---

## 生物 (在生態瓶內)

### `GET /api/terrariums/{terrarium_id}/creatures`

生物名稱到狀態 dict 的 map。

### `POST /api/terrariums/{terrarium_id}/creatures`

執行期加一隻生物。

- Body：`CreatureAdd` (`name`、`config_path`、`listen_channels`、`send_channels`)。
- Response：`{"creature": <name>, "status": "running"}`。

### `DELETE /api/terrariums/{terrarium_id}/creatures/{name}`

移除一隻生物。Response：`{"status": "removed"}`。

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/interrupt`

打斷生物當前的 `agent.process()`，但不終止生物本身。Response：`{"status": "interrupted", "creature": <name>}`。

### `GET /api/terrariums/{terrarium_id}/creatures/{name}/jobs`

執行中與排隊中的背景 job。

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/tasks/{job_id}/stop`

取消執行中的背景 job。Response：`{"status": "cancelled", "job_id"}`。

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/promote/{job_id}`

把一個 direct task 升級到背景佇列。

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/model`

不重啟切換生物的 LLM。

- Body：`ModelSwitch` (`model`)。
- Response：`{"status": "switched", "creature", "model"}`。

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/wire`

替生物加一條 listen 或 send 綁定。

- Body：`WireChannel` (`channel`、`direction` = `"listen"` 或 `"send"`)。
- Response：`{"status": "wired"}`。

---

## 獨立代理

### `POST /api/agents`

在任何生態瓶之外建一個代理並啟動。

- Body：`AgentCreate` (`config_path`、選用 `llm`、`pwd`)。
- Response：`{"agent_id", "status": "running"}`。

### `GET /api/agents`

列出執行中的代理。

### `GET /api/agents/{agent_id}`

回 `{"agent_id", "name", "model", "running", "is_processing", ...}`。status payload 也包含工具/子代理與 context 細節。

### `DELETE /api/agents/{agent_id}`

停下代理。Response：`{"status": "stopped"}`。

### `POST /api/agents/{agent_id}/interrupt`

打斷當前處理。

### `POST /api/agents/{agent_id}/regenerate`

用目前 model/settings 重跑上一則 assistant 回應。Response：`{"status": "regenerating"}`。

### `POST /api/agents/{agent_id}/messages/{msg_idx}/edit`

改一則 user message 並從該點重播。

- Body：`MessageEdit` (`content`)。
- Response：`{"status": "edited"}`。
- Side effect：從 `msg_idx` 截斷歷程、注入新訊息、重播。

### `POST /api/agents/{agent_id}/messages/{msg_idx}/rewind`

只截斷對話，不重跑。Response：`{"status": "rewound"}`。

### `POST /api/agents/{agent_id}/promote/{job_id}`

把 direct task 升級到背景。

### `GET /api/agents/{agent_id}/plugins`

列外掛與狀態。

### `POST /api/agents/{agent_id}/plugins/{plugin_name}/toggle`

啟用/停用外掛。Response：`{"name", "enabled"}`。

### `GET /api/agents/{agent_id}/jobs`

列背景 job。

### `POST /api/agents/{agent_id}/tasks/{job_id}/stop`

取消背景 job。

### `GET /api/agents/{agent_id}/history`

回 `{"agent_id", "events": [...], "is_processing": bool}`。事件流包含 sibling branches；client 可本地 replay 目前 branch，或呼叫 `/branches` 取得 compact branch map。

### `GET /api/agents/{agent_id}/branches`

回 branch navigator 使用的逐 turn branch metadata：
`{"agent_id": str, "turns": [{"turn_index": int, "branches": [int], "latest_branch": int}]}`。

### `POST /api/agents/{agent_id}/model`

切換代理 LLM。

- Body：`ModelSwitch` (`model`)。
- Response：`{"status": "switched", "model"}`。

### `POST /api/agents/{agent_id}/command`

執行一個 user slash 指令 (例如 `model`、`status`)。

- Body：`SlashCommand` (`command`、選用 `args`)。
- Response：隨指令而定。

### `POST /api/agents/{agent_id}/chat`

非串流 chat。

- Body：`AgentChat`。
- Response：`{"response": <完整文字>}`。

### `GET /api/agents/{agent_id}/scratchpad`

回草稿區 key-value map。

### `PATCH /api/agents/{agent_id}/scratchpad`

- Body：`ScratchpadPatch`。
- Response：更新後的草稿區。

### `GET /api/agents/{agent_id}/triggers`

活著的觸發器：`[{trigger_id, trigger_type, running, created_at}]`。

### `GET /api/agents/{agent_id}/env`

回 `{"pwd", "env"}`，敏感欄位會濾掉。

### `GET /api/agents/{agent_id}/system-prompt`

回 `{"text": <system prompt>}`。

---

## Config 探索

### `GET /api/configs/creatures`

列出可發現的生物 config：`[{"name", "path", "description"}]`。路徑可能是絕對路徑或套件參照。

### `GET /api/configs/terrariums`

列出可發現的生態瓶 config (形狀同上)。

### `GET /api/configs/server-info`

回 `{"cwd", "platform"}`。

### `GET /api/configs/models`

列出每個設好的 LLM model/profile 與可用狀態。

### `GET /api/configs/commands`

列 slash 指令：`[{"name", "aliases", "description", "layer"}]`。

---

## Registry 與套件管理

### `GET /api/registry`

掃本地資料夾與已安裝套件。回 `[{"name", "type", "description", "model", "tools", "path", "source", ...}]`。`source` 是 `"local"` 或套件名。

### `GET /api/registry/remote`

從內附的 `registry.json` 回 `{"repos": [...]}`。

### `POST /api/registry/install`

- Body：`InstallRequest` (`url`、選用 `name`)。
- Response：`{"status": "installed", "name"}`。

### `POST /api/registry/uninstall`

- Body：`UninstallRequest` (`name`)。
- Response：`{"status": "uninstalled", "name"}`。

---

## 工作階段

### `GET /api/sessions`

列出已存的工作階段。

Query 參數：

| 參數 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `limit` | int | `20` | 最多回傳幾個。 |
| `offset` | int | `0` | 跳過 N 個。 |
| `search` | str | — | 依名字、config、代理、preview 過濾 (不分大小寫)。 |
| `refresh` | bool | `false` | 強制重建索引。 |

Response：

```json
{
  "sessions": [
    {
      "name": "...", "filename": "...", "config_type": "agent|terrarium",
      "config_path": "...", "agents": [...], "terrarium_name": "...",
      "status": "...", "created_at": "...", "last_active": "...",
      "preview": "...", "pwd": "..."
    }
  ],
  "total": 123,
  "offset": 0,
  "limit": 20
}
```

Side effect：索引在第一次請求或 30 秒過後會重建。

### `DELETE /api/sessions/{session_name}`

刪掉一個工作階段檔。Response：`{"status": "deleted", "name"}`。接受 stem 或完整檔名。

### `POST /api/sessions/{session_name}/resume`

恢復一個已存的工作階段。

- Response：`{"instance_id", "type": "agent"|"terrarium", "session_name"}`。
- 狀態碼：`200`、`400` (前綴有歧義)、`404`、`500`。

### `GET /api/sessions/{session_name}/history`

工作階段 metadata 與可用 target。

- Response：`{"session_name", "meta", "targets"}`，targets 包含代理名稱、`"root"`、`"ch:<channel>"`。

### `GET /api/sessions/{session_name}/history/{target}`

唯讀的已存歷程。`target` 要 URL-encode，接受 `"root"`、生物名稱、或 `"ch:<channel_name>"`。

- Response：`{"session_name", "target", "meta", "messages", "events"}`。

### `GET /api/sessions/{session_name}/memory/search`

在已存工作階段上跑 FTS5 / semantic / hybrid 搜尋。

Query 參數：

| 參數 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `q` | str | 必填 | Query。 |
| `mode` | `auto\|fts\|semantic\|hybrid` | `auto` | 搜尋模式。 |
| `k` | int | `10` | 最多回幾筆。 |
| `agent` | str | — | 依代理過濾。 |

Response：`{"session_name", "query", "mode", "k", "count", "results"}`。每筆 result：`{content, round, block, agent, block_type, score, ts, tool_name, channel}`。

Side effect：未建索引的事件會索引起來 (冪等)；代理執行中會用 live embedder，否則從 config 載入。

---

## 檔案

### `GET /api/files/tree`

巢狀檔案樹。

Query 參數：`root` (必填)、`depth` (預設 `3`，限制在 `1..10`)。

Response：遞迴物件 `{"name", "path", "type": "directory"|"file", "children": [...], "size"}`。

### `GET /api/files/browse`

檔案系統 UI 用的目錄瀏覽。

Query 參數：`path` (選用)。

Response：`{"current": {...}, "parent": str|null, "roots": [...], "directories": [...]}`。

### `GET /api/files/read`

讀一個文字檔。

- Query 參數：`path` (必填)。
- Response：`{"path", "content", "size", "modified", "language"}`。
- 錯誤：binary 檔、權限不足 → `400`；不存在 → `404`。

### `POST /api/files/write`

- Body：`FileWrite` (`path`、`content`)。
- Response：`{"success": true, "size"}`。
- Side effect：會自動建父層目錄。

### `POST /api/files/rename`

- Body：`FileRename` (`old_path`、`new_path`)。
- Response：`{"success": true}`。

### `POST /api/files/delete`

刪檔或空資料夾。

- Body：`FileDelete` (`path`)。
- Response：`{"success": true}`。

### `POST /api/files/mkdir`

遞迴 mkdir。

- Body：`FileMkdir` (`path`)。
- Response：`{"success": true}`。

---

## 設定

### API key

#### `GET /api/settings/keys`

回 `{"providers": [{"provider", "backend_type", "env_var", "has_key", "masked_key", "available", "built_in"}]}`。

#### `POST /api/settings/keys`

- Body：`ApiKeyRequest` (`provider`、`key`)。
- Response：`{"status": "saved", "provider"}`。

#### `DELETE /api/settings/keys/{provider}`

Response：`{"status": "removed", "provider"}`。

### Codex

#### `POST /api/settings/codex-login`

在 server 端跑 Codex OAuth 流程 (server 必須是本機)。Response：`{"status": "ok", "expires_at"}`。

#### `GET /api/settings/codex-status`

回 `{"authenticated", "expired"?}`。

#### `GET /api/settings/codex-usage`

抓 Codex 過去 14 天的用量。狀態：`200`、`401` (token refresh 失敗)、`404` (沒登入)。

### Backend

#### `GET /api/settings/backends`

`{"backends": [{"name", "backend_type", "base_url", "api_key_env", "built_in", "has_token", "available"}]}`。

#### `POST /api/settings/backends`

- Body：`BackendRequest` (`name`、`backend_type` 預設 `"openai"`、`base_url`、`api_key_env`)。
- Response：`{"status": "saved", "name"}`。

#### `DELETE /api/settings/backends/{name}`

Response：`{"status": "deleted", "name"}`。內建 backend 不能刪 (`400`)。

### Profile

#### `GET /api/settings/profiles`

`{"profiles": [...]}`，欄位：`name, model, provider, backend_type, base_url, api_key_env, max_context, max_output, temperature, reasoning_effort, service_tier, extra_body`。

#### `POST /api/settings/profiles`

- Body：`ProfileRequest`。
- Response：`{"status": "saved", "name"}`。

#### `DELETE /api/settings/profiles/{name}`

Response：`{"status": "deleted", "name"}`。

#### `GET /api/settings/default-model`

`{"default_model"}`。

#### `POST /api/settings/default-model`

- Body：`DefaultModelRequest` (`name`)。
- Response：`{"status": "set", "default_model"}`。

#### `GET /api/settings/models`

同 `GET /api/configs/models`。

### UI prefs

#### `GET /api/settings/ui-prefs`

`{"values": {...}}`。

#### `POST /api/settings/ui-prefs`

- Body：`UIPrefsUpdateRequest` (`values`)。
- Response：`{"values": <合併後>}`。

### MCP

#### `GET /api/settings/mcp`

`{"servers": [{"name", "transport", "command", "args", "env", "url"}]}`。

#### `POST /api/settings/mcp`

- Body：`MCPServerRequest`。
- Response：`{"status": "saved", "name"}`。

#### `DELETE /api/settings/mcp/{name}`

Response：`{"status": "removed", "name"}`。

---

## WebSocket 端點

所有 WebSocket 端點都是雙向的，走標準 upgrade (沒有自訂 header 或 subprotocol)。Client 收到一串 JSON frame，可以送 input frame。Server 出錯會關連線；沒有自動重連、沒有 heartbeat — client 自己負責。

### `WS /ws/terrariums/{terrarium_id}`

整個生態瓶 (root + 生物 + 頻道) 的統一事件流。

送入 frame：

- `{"type": "input", "target": "root"|<creature>, "content": str|list[dict], "message"?: str}` — 把 input 排進目標佇列。Server 用 `{"type": "idle", "source": <target>, "ts": float}` 回應。
- 其他 message type 會被忽略。

送出 frame：

- `{"type": "activity", "activity_type": ..., "source", "ts", ...}` — activity type 包含 `session_info`、`tool_call`、`tool_result`、`token_usage`、`job_update`、`job_completed` 等等 (見 [事件型別](#事件型別))。
- `{"type": "text", "content", "source", "ts"}` — 串流文字 chunk。
- `{"type": "processing_start", "source", "ts"}`。
- `{"type": "processing_end", "source", "ts"}`。
- `{"type": "channel_message", "source": "channel", "channel", "sender", "content", "message_id", "timestamp", "ts", "history"?: bool}` — 重播連線前的舊訊息時 `history` 為 `true`。
- `{"type": "error", "content", "source"?, "ts"}`。
- `{"type": "idle", "source"?, "ts"}`。

生命週期：

- 連線立刻接受；生態瓶不存在 → upgrade 前 `404`。
- 先重播頻道歷程。
- 之後即時串流事件。
- Client 關閉是 graceful；清理時會卸下輸出並移除 callback。

### `WS /ws/creatures/{agent_id}`

獨立代理的事件流。

送入 frame：`{"type": "input", "content": str|list[dict], "message"?: str}`。

送出 frame：跟生態瓶流一樣的 `activity` / `text` / `processing_*` / `error` / `idle` 家族。第一個事件一定是 `{"type": "activity", "activity_type": "session_info", "source", "model", "agent_name", "ts"}`。

### `WS /ws/agents/{agent_id}/chat`

較單純的 request-response chat 通道。

送入：`{"message": str}`。

送出：`{"type": "text", "content"}`、`{"type": "done"}`、`{"type": "error", "content"}`。

會跨多個回合保持開啟。

### `WS /ws/terrariums/{terrarium_id}/channels`

生態瓶的唯讀頻道 feed。

送出：`{"type": "channel_message", "channel", "sender", "content", "message_id", "timestamp"}`。

### `WS /ws/files/{agent_id}`

對代理工作目錄做檔案變動監看。

送出：

- `{"type": "ready", "root"}` — watcher 已啟動。
- `{"type": "change", "changes": [{"path", "abs_path", "action": "added"|"modified"|"deleted"}]}` — 每秒批次一次。隱藏 / 被忽略的資料夾 (`.git`、`node_modules`、`__pycache__`、`.venv`、`.mypy_cache` 等) 會被過濾。
- `{"type": "error", "text"}`。

### `WS /ws/logs`

Server 程序 log 檔的即時 tail。

送出：

- `{"type": "meta", "path", "pid"}` — 連上時送。
- `{"type": "line", "ts", "level", "module", "text"}` — 串流。
- `{"type": "error", "text"}`。

Server 會先重播最後 ~200 行，再開始串流新行。

### `WS /ws/terminal/{agent_id}`

代理工作目錄下的互動式 PTY。

送入：

- `{"type": "input", "data": str}` — shell 輸入 (要送出請在尾端加 `\n`)。
- `{"type": "resize", "rows": int, "cols": int}`。

送出：

- `{"type": "output", "data": str}` (UTF-8；不合法序列會被取代)。
- `{"type": "error", "data": str}`。

實作：

- Unix：`pty.openpty()` + fork + exec。
- Windows 配 `winpty`：ConPTY。
- Fallback：沒有 PTY 的純 pipe。
- 連上時送一個初始 `{"type": "output", "data": ""}`。
- 清理時：SIGTERM 然後 SIGKILL。

### `WS /ws/terminal/terrariums/{terrarium_id}/{target}`

跟每隻代理的 terminal 一樣，但在生態瓶裡解析生物名或 `"root"`。

---

## Schema

Request / response 用到的 Pydantic 模型。

### `TerrariumCreate`

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `config_path` | str | 是 | |
| `llm` | str \| None | 否 | |
| `pwd` | str \| None | 否 | |

### `TerrariumStatus`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `terrarium_id` | str | 是 |
| `name` | str | 是 |
| `running` | bool | 是 |
| `creatures` | dict | 是 |
| `channels` | list | 是 |

### `CreatureAdd`

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `name` | str | 是 | |
| `config_path` | str | 是 | |
| `listen_channels` | list[str] | 否 | `[]` |
| `send_channels` | list[str] | 否 | `[]` |

### `ChannelAdd`

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `name` | str | 是 | |
| `channel_type` | str | 否 | `"queue"` |
| `description` | str | 否 | `""` |

### `ChannelSend`

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `content` | `str \| list[ContentPartPayload]` | 是 | |
| `sender` | str | 否 | `"human"` |

### `WireChannel`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `channel` | str | 是 |
| `direction` | `"listen" \| "send"` | 是 |

### `AgentCreate`

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `config_path` | str | 是 | |
| `llm` | str \| None | 否 | |
| `pwd` | str \| None | 否 | |

### `AgentChat`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `message` | str \| None | 否 |
| `content` | list[ContentPartPayload] \| None | 否 |

`message` 或 `content` 至少給一個。

### `MessageEdit`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `content` | str | 是 |

### `SlashCommand`

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `command` | str | 是 | |
| `args` | str | 否 | `""` |

### `ModelSwitch`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `model` | str | 是 |

### `FileWrite`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `path` | str | 是 |
| `content` | str | 是 |

### `FileRename`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `old_path` | str | 是 |
| `new_path` | str | 是 |

### `FileDelete`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `path` | str | 是 |

### `FileMkdir`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `path` | str | 是 |

### Content parts

`ContentPartPayload` 是 `TextPartPayload`、`ImagePartPayload`、`FilePartPayload` 的 discriminated union。

**`TextPartPayload`**

| 欄位 | 型別 | 必要 |
|---|---|---|
| `type` | `"text"` | 是 |
| `text` | str | 是 |

**`ImageUrlPayload`**

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `url` | str | 是 | |
| `detail` | `"auto" \| "low" \| "high"` | 否 | `"low"` |

**`ContentMetaPayload`**

| 欄位 | 型別 | 必要 |
|---|---|---|
| `source_type` | str \| None | 否 |
| `source_name` | str \| None | 否 |

**`ImagePartPayload`**

| 欄位 | 型別 | 必要 |
|---|---|---|
| `type` | `"image_url"` | 是 |
| `image_url` | ImageUrlPayload | 是 |
| `meta` | ContentMetaPayload \| None | 否 |

**`FilePayload`**

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `path` | str \| None | 否 | |
| `name` | str \| None | 否 | |
| `content` | str \| None | 否 | |
| `mime` | str \| None | 否 | |
| `data_base64` | str \| None | 否 | |
| `encoding` | `"utf-8" \| "base64" \| None` | 否 | |
| `is_inline` | bool | 否 | `False` |

**`FilePartPayload`**

| 欄位 | 型別 | 必要 |
|---|---|---|
| `type` | `"file"` | 是 |
| `file` | FilePayload | 是 |

### `ScratchpadPatch`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `updates` | dict[str, str \| None] | 是 |

`null` 代表刪掉該 key。

### `ApiKeyRequest`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `provider` | str | 是 |
| `key` | str | 是 |

### `ProfileRequest`

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `name` | str | 是 | |
| `model` | str | 是 | |
| `provider` | str | 否 | `""` |
| `max_context` | int | 否 | `128000` |
| `max_output` | int | 否 | `16384` |
| `temperature` | float \| None | 否 | |
| `reasoning_effort` | str | 否 | `""` |
| `service_tier` | str | 否 | `""` |
| `extra_body` | dict \| None | 否 | |

### `BackendRequest`

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `name` | str | 是 | |
| `backend_type` | str | 否 | `"openai"` |
| `base_url` | str | 否 | `""` |
| `api_key_env` | str | 否 | `""` |

### `DefaultModelRequest`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `name` | str | 是 |

### `UIPrefsUpdateRequest`

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `values` | dict[str, Any] | 否 | `{}` |

### `InstallRequest`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `url` | str | 是 |
| `name` | str \| None | 否 |

### `UninstallRequest`

| 欄位 | 型別 | 必要 |
|---|---|---|
| `name` | str | 是 |

### `MCPServerRequest`

| 欄位 | 型別 | 必要 | 預設 |
|---|---|---|---|
| `name` | str | 是 | |
| `transport` | str | 否 | `"stdio"` |
| `command` | str | 否 | `""` |
| `args` | list[str] | 否 | `[]` |
| `env` | dict[str, str] | 否 | `{}` |
| `url` | str | 否 | `""` |

---

## 事件型別

事件會持久化到 `SessionStore`，並透過 WebSocket 串流。每個事件都帶 `type`、`source` (來源代理/生物名稱)、`ts` (Unix 秒)。

- `text` — 串流文字 chunk。
  - `content: str`。
- `activity` — 多種類型，以 `activity_type` 區分，例如 `session_info`、`tool_call`、`tool_result`、`token_usage`、`job_update`、`job_completed`、`model_switch`、`interrupt`、`regenerate`、`edit`、`rewind`、`promote`、`background_result`、`memory_compact`、`memory_search`、`memory_save`。
  - 其他欄位視 `activity_type` 而定：`args`、`job_id`、`tools_used`、`result`、`output`、`turns`、`duration`、`task`、`trigger_id`、`event_type`、`channel`、`sender`、`content`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`cached_tokens`、`round`、`summary`、`messages_compacted`、`session_id`、`model`、`agent_name`、`max_context`、`compact_threshold`、`error_type`、`error`、`messages_cleared`、`background`、`subagent`、`tool`、`interrupted`、`final_state`。
- `processing_start`、`processing_end`。
- `user_input` — `content: str | list[dict]`。
- `channel_message` — `channel`、`sender`、`content`、`message_id`、`timestamp`。

## 工作階段儲存

工作階段放在 `~/.kohakuterrarium/sessions/`，副檔名 `.kohakutr` (舊的 `.kt` 也接受)。資料表結構與 resume 路徑見 [concepts/impl-notes/session-persistence](../concepts/impl-notes/session-persistence.md)。

## 給整合者的補充

- HTTP chat 端點是非串流的。要串流請用對應的 WebSocket。
- `/ws/terrariums/{id}` 與 `/ws/terrariums/{id}/channels` 連上時都會帶頻道歷程；舊訊息 frame 會帶 `"history": true`。
- `/ws/files/{agent_id}` 需要代理有工作目錄。
- Terminal client 在本地 terminal resize 時必須送 `resize` frame。

## 延伸閱讀

- 概念：[工作階段持久化](../concepts/impl-notes/session-persistence.md)、[邊界](../concepts/boundaries.md)。
- 指南：[程式化使用](../guides/programmatic-usage.md)、[前端版面](../guides/frontend-layout.md)、[工作階段](../guides/sessions.md)。
- 參考：[CLI](cli.md)、[Python API](python.md)、[設定](configuration.md)。
