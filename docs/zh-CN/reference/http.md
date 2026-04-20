---
title: HTTP API
summary: kt serve 的 REST 端点与 WebSocket 通道，含 request / response 结构。
tags:
  - reference
  - http
  - api
---

# HTTP 与 WebSocket API

套件内置 FastAPI server (`kt web`、`kt serve`、`python -m kohakuterrarium.api.main`) 暴露的所有 REST 端点与 WebSocket 通道。这个 API 是 Vue SPA 的后端，任何想从程序外控制代理与Terrarium的 client 都可以用它。

Serving 层和会话存储的结构请参见 [会话持久化概念](../concepts/impl-notes/session-persistence.md)。任务导向的用法请参见 [编程方式使用指南](../guides/programmatic-usage.md) 和 [前端布局指南](../guides/frontend-layout.md)。

## Server 配置

- 默认 host：`0.0.0.0`。
- 默认 port：`8001` (`kt web` 下面被占用会自动递增)。
- 覆盖：`python -m kohakuterrarium.api.main --host 127.0.0.1 --port 8080 [--reload]`。
- `KT_SESSION_DIR` 覆盖默认会话目录。
- CORS 全开：`allow_origins=["*"]`、所有方法、所有 header。
- 没有验证。把这个 server 当成被信任的本机服务处理。
- 版本字符串：`0.1.0`。没有 `/v1/` URL prefix。
- FastAPI auto-docs：`/docs` (Swagger UI)、`/redoc` (ReDoc)。

当 `create_app(static_dir=Path)` 收到有效的 SPA build 目录时：

- `/assets/*` — 带 hash 的 build 资产。
- `/{path}` — SPA fallback，对任何未比对的路径送 `index.html`。
- `/api/*` 与 WebSocket 路由优先。

## Response 惯例

- 状态码：`200` 成功、`400` 输入错误、`404` 资源不存在、`500` server error。不用 `201`。
- Payload 除非另注明，都是 JSON。
- 错误用 FastAPI `HTTPException`：`{"detail": "<message>"}`。

---

## Terrarium

### `POST /api/terrariums`

从 config 路径建一个Terrarium并启动。

- Body：`TerrariumCreate` (`config_path`、选用 `llm`、`pwd`)。
- Response：`{"terrarium_id": str, "status": "running"}`。
- 状态：`200`、`400`。
- Side effect：生出Terrarium、初始化 root 代理、启动Creature、设了就开 session store。

### `GET /api/terrariums`

列出所有执行中的Terrarium，回一个状态对象 array (形状同下面的单一 terrarium GET)。

### `GET /api/terrariums/{terrarium_id}`

回 `TerrariumStatus`：`terrarium_id`、`name`、`running`、`creatures` (name → status dict)、`channels` (频道名称清单)。

### `DELETE /api/terrariums/{terrarium_id}`

停下并清理Terrarium。Response：`{"status": "stopped"}`。Side effect：所有Creature停掉、频道清掉、session store 关闭。

### `POST /api/terrariums/{terrarium_id}/channels`

执行期加一条频道。

- Body：`ChannelAdd` (`name`、`channel_type` 默认 `"queue"`、`description`)。
- Response：`{"status": "created", "channel": <name>}`。

### `GET /api/terrariums/{terrarium_id}/channels`

列频道：`[{"name", "type", "description"}]`。

### `POST /api/terrariums/{terrarium_id}/channels/{channel_name}/send`

往频道塞一则消息。

- Body：`ChannelSend` (`content` 可以是 `str` 或 `list[ContentPartPayload]`、`sender` 默认 `"human"`)。
- Response：`{"message_id": str, "status": "sent"}`。
- Side effect：消息写入历程、listener 触发 `on_send` callback。

### `POST /api/terrariums/{terrarium_id}/chat/{target}`

非流式 chat。`target` 可以是 `"root"` 或Creature名称。

- Body：`AgentChat` (`message` 或 `content`)。
- Response：`{"response": <完整文本>}`。

### `GET /api/terrariums/{terrarium_id}/history/{target}`

读对话与事件日志。`target` 是 `"root"`、Creature名称、或 `"ch:<channel_name>"` (频道历程)。优先用 SessionStore，失败就 fallback 到记忆体 log。

- Response：`{"terrarium_id", "target", "messages": [...], "events": [...]}`。

### `GET /api/terrariums/{terrarium_id}/scratchpad/{target}`

回目标代理的草稿区，形式是 `{key: value}`。

### `PATCH /api/terrariums/{terrarium_id}/scratchpad/{target}`

- Body：`ScratchpadPatch` (`updates: {key: value | null}`；`null` 表示删除)。
- Response：更新后的草稿区。

### `GET /api/terrariums/{terrarium_id}/triggers/{target}`

列出活着的 remote trigger：`[{"trigger_id", "trigger_type", "running", "created_at"}]`。

### `GET /api/terrariums/{terrarium_id}/plugins/{target}`

列出已加载的插件与启用状态。

### `POST /api/terrariums/{terrarium_id}/plugins/{target}/{plugin_name}/toggle`

切换插件启用状态。Response：`{"name", "enabled"}`。启用时会调用 `load_pending()`。

### `GET /api/terrariums/{terrarium_id}/env/{target}`

回 `{"pwd", "env"}`；env 里含有 `secret`、`key`、`token`、`password`、`pass`、`private`、`auth`、`credential` 等字样 (不分大小写) 的 key 会被滤掉。

### `GET /api/terrariums/{terrarium_id}/system-prompt/{target}`

回 `{"text": <组装后的 system prompt>}`。

---

## Creature (在Terrarium内)

### `GET /api/terrariums/{terrarium_id}/creatures`

Creature名称到状态 dict 的 map。

### `POST /api/terrariums/{terrarium_id}/creatures`

执行期加一只Creature。

- Body：`CreatureAdd` (`name`、`config_path`、`listen_channels`、`send_channels`)。
- Response：`{"creature": <name>, "status": "running"}`。

### `DELETE /api/terrariums/{terrarium_id}/creatures/{name}`

移除一只Creature。Response：`{"status": "removed"}`。

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/interrupt`

打断Creature当前的 `agent.process()`，但不终止Creature本身。Response：`{"status": "interrupted", "creature": <name>}`。

### `GET /api/terrariums/{terrarium_id}/creatures/{name}/jobs`

执行中与排队中的背景 job。

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/tasks/{job_id}/stop`

取消执行中的背景 job。Response：`{"status": "cancelled", "job_id"}`。

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/promote/{job_id}`

把一个 direct task 升级到背景队列。

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/model`

不重启切换Creature的 LLM。

- Body：`ModelSwitch` (`model`)。
- Response：`{"status": "switched", "creature", "model"}`。

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/wire`

替Creature加一条 listen 或 send 绑定。

- Body：`WireChannel` (`channel`、`direction` = `"listen"` 或 `"send"`)。
- Response：`{"status": "wired"}`。

---

## 独立代理

### `POST /api/agents`

在任何Terrarium之外建一个代理并启动。

- Body：`AgentCreate` (`config_path`、选用 `llm`、`pwd`)。
- Response：`{"agent_id", "status": "running"}`。

### `GET /api/agents`

列出执行中的代理。

### `GET /api/agents/{agent_id}`

回 `{"agent_id", "name", "model", "running"}`。

### `DELETE /api/agents/{agent_id}`

停下代理。Response：`{"status": "stopped"}`。

### `POST /api/agents/{agent_id}/interrupt`

打断当前处理。

### `POST /api/agents/{agent_id}/regenerate`

用目前 model/settings 重跑上一则 assistant 回应。Response：`{"status": "regenerating"}`。

### `POST /api/agents/{agent_id}/messages/{msg_idx}/edit`

改一则 user message 并从该点重播。

- Body：`MessageEdit` (`content`)。
- Response：`{"status": "edited"}`。
- Side effect：从 `msg_idx` 截断历程、注入新消息、重播。

### `POST /api/agents/{agent_id}/messages/{msg_idx}/rewind`

只截断对话，不重跑。Response：`{"status": "rewound"}`。

### `POST /api/agents/{agent_id}/promote/{job_id}`

把 direct task 升级到背景。

### `GET /api/agents/{agent_id}/plugins`

列插件与状态。

### `POST /api/agents/{agent_id}/plugins/{plugin_name}/toggle`

启用/停用插件。Response：`{"name", "enabled"}`。

### `GET /api/agents/{agent_id}/jobs`

列背景 job。

### `POST /api/agents/{agent_id}/tasks/{job_id}/stop`

取消背景 job。

### `GET /api/agents/{agent_id}/history`

回 `{"agent_id", "events": [...]}`。

### `POST /api/agents/{agent_id}/model`

切换代理 LLM。

- Body：`ModelSwitch` (`model`)。
- Response：`{"status": "switched", "model"}`。

### `POST /api/agents/{agent_id}/command`

执行一个 user slash 指令 (例如 `model`、`status`)。

- Body：`SlashCommand` (`command`、选用 `args`)。
- Response：随指令而定。

### `POST /api/agents/{agent_id}/chat`

非流式 chat。

- Body：`AgentChat`。
- Response：`{"response": <完整文本>}`。

### `GET /api/agents/{agent_id}/scratchpad`

回草稿区 key-value map。

### `PATCH /api/agents/{agent_id}/scratchpad`

- Body：`ScratchpadPatch`。
- Response：更新后的草稿区。

### `GET /api/agents/{agent_id}/triggers`

活着的trigger：`[{trigger_id, trigger_type, running, created_at}]`。

### `GET /api/agents/{agent_id}/env`

回 `{"pwd", "env"}`，敏感字段会滤掉。

### `GET /api/agents/{agent_id}/system-prompt`

回 `{"text": <system prompt>}`。

---

## Config 探索

### `GET /api/configs/creatures`

列出可发现的Creature config：`[{"name", "path", "description"}]`。路径可能是绝对路径或套件参照。

### `GET /api/configs/terrariums`

列出可发现的Terrarium config (形状同上)。

### `GET /api/configs/server-info`

回 `{"cwd", "platform"}`。

### `GET /api/configs/models`

列出每个设好的 LLM model/profile 与可用状态。

### `GET /api/configs/commands`

列 slash 指令：`[{"name", "aliases", "description", "layer"}]`。

---

## Registry 与套件管理

### `GET /api/registry`

扫本地目录与已安装套件。回 `[{"name", "type", "description", "model", "tools", "path", "source", ...}]`。`source` 是 `"local"` 或套件名。

### `GET /api/registry/remote`

从内附的 `registry.json` 回 `{"repos": [...]}`。

### `POST /api/registry/install`

- Body：`InstallRequest` (`url`、选用 `name`)。
- Response：`{"status": "installed", "name"}`。

### `POST /api/registry/uninstall`

- Body：`UninstallRequest` (`name`)。
- Response：`{"status": "uninstalled", "name"}`。

---

## 会话

### `GET /api/sessions`

列出已存的会话。

Query 参数：

| 参数 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `limit` | int | `20` | 最多返回几个。 |
| `offset` | int | `0` | 跳过 N 个。 |
| `search` | str | — | 依名字、config、代理、preview 过滤 (不分大小写)。 |
| `refresh` | bool | `false` | 强制重建索引。 |

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

Side effect：索引在第一次请求或 30 秒过后会重建。

### `DELETE /api/sessions/{session_name}`

删掉一个会话档。Response：`{"status": "deleted", "name"}`。接受 stem 或完整档名。

### `POST /api/sessions/{session_name}/resume`

恢复一个已存的会话。

- Response：`{"instance_id", "type": "agent"|"terrarium", "session_name"}`。
- 状态码：`200`、`400` (前缀有歧义)、`404`、`500`。

### `GET /api/sessions/{session_name}/history`

会话 metadata 与可用 target。

- Response：`{"session_name", "meta", "targets"}`，targets 包含代理名称、`"root"`、`"ch:<channel>"`。

### `GET /api/sessions/{session_name}/history/{target}`

只读的已存历程。`target` 要 URL-encode，接受 `"root"`、Creature名称、或 `"ch:<channel_name>"`。

- Response：`{"session_name", "target", "meta", "messages", "events"}`。

### `GET /api/sessions/{session_name}/memory/search`

在已存会话上跑 FTS5 / semantic / hybrid 搜索。

Query 参数：

| 参数 | 型别 | 默认 | 说明 |
|---|---|---|---|
| `q` | str | 必填 | Query。 |
| `mode` | `auto\|fts\|semantic\|hybrid` | `auto` | 搜索模式。 |
| `k` | int | `10` | 最多回几笔。 |
| `agent` | str | — | 依代理过滤。 |

Response：`{"session_name", "query", "mode", "k", "count", "results"}`。每笔 result：`{content, round, block, agent, block_type, score, ts, tool_name, channel}`。

Side effect：未建索引的事件会索引起来 (幂等)；代理执行中会用 live embedder，否则从 config 加载。

---

## 文件

### `GET /api/files/tree`

嵌套文件树。

Query 参数：`root` (必填)、`depth` (默认 `3`，限制在 `1..10`)。

Response：递回对象 `{"name", "path", "type": "directory"|"file", "children": [...], "size"}`。

### `GET /api/files/browse`

文件系统 UI 用的目录浏览。

Query 参数：`path` (选用)。

Response：`{"current": {...}, "parent": str|null, "roots": [...], "directories": [...]}`。

### `GET /api/files/read`

读一个文本档。

- Query 参数：`path` (必填)。
- Response：`{"path", "content", "size", "modified", "language"}`。
- 错误：binary 档、权限不足 → `400`；不存在 → `404`。

### `POST /api/files/write`

- Body：`FileWrite` (`path`、`content`)。
- Response：`{"success": true, "size"}`。
- Side effect：会自动建父层目录。

### `POST /api/files/rename`

- Body：`FileRename` (`old_path`、`new_path`)。
- Response：`{"success": true}`。

### `POST /api/files/delete`

删档或空目录。

- Body：`FileDelete` (`path`)。
- Response：`{"success": true}`。

### `POST /api/files/mkdir`

递回 mkdir。

- Body：`FileMkdir` (`path`)。
- Response：`{"success": true}`。

---

## 配置

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

在 server 端跑 Codex OAuth 流程 (server 必须是本机)。Response：`{"status": "ok", "expires_at"}`。

#### `GET /api/settings/codex-status`

回 `{"authenticated", "expired"?}`。

#### `GET /api/settings/codex-usage`

抓 Codex 过去 14 天的用量。状态：`200`、`401` (token refresh 失败)、`404` (没登入)。

### Backend

#### `GET /api/settings/backends`

`{"backends": [{"name", "backend_type", "base_url", "api_key_env", "built_in", "has_token", "available"}]}`。

#### `POST /api/settings/backends`

- Body：`BackendRequest` (`name`、`backend_type` 默认 `"openai"`、`base_url`、`api_key_env`)。
- Response：`{"status": "saved", "name"}`。

#### `DELETE /api/settings/backends/{name}`

Response：`{"status": "deleted", "name"}`。内置 backend 不能删 (`400`)。

### Profile

#### `GET /api/settings/profiles`

`{"profiles": [...]}`，字段：`name, model, provider, backend_type, base_url, api_key_env, max_context, max_output, temperature, reasoning_effort, service_tier, extra_body`。

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
- Response：`{"values": <合并后>}`。

### MCP

#### `GET /api/settings/mcp`

`{"servers": [{"name", "transport", "command", "args", "env", "url"}]}`。

#### `POST /api/settings/mcp`

- Body：`MCPServerRequest`。
- Response：`{"status": "saved", "name"}`。

#### `DELETE /api/settings/mcp/{name}`

Response：`{"status": "removed", "name"}`。

---

## WebSocket 端点

所有 WebSocket 端点都是双向的，走标准 upgrade (没有自定义 header 或 subprotocol)。Client 收到一串 JSON frame，可以送 input frame。Server 出错会关连线；没有自动重连、没有 heartbeat — client 自己负责。

### `WS /ws/terrariums/{terrarium_id}`

整个Terrarium (root + Creature + 频道) 的统一事件流。

送入 frame：

- `{"type": "input", "target": "root"|<creature>, "content": str|list[dict], "message"?: str}` — 把 input 排进目标队列。Server 用 `{"type": "idle", "source": <target>, "ts": float}` 回应。
- 其他 message type 会被忽略。

送出 frame：

- `{"type": "activity", "activity_type": ..., "source", "ts", ...}` — activity type 包含 `session_info`、`tool_call`、`tool_result`、`token_usage`、`job_update`、`job_completed` 等等 (见 [事件型别](#事件型别))。
- `{"type": "text", "content", "source", "ts"}` — 流式文本 chunk。
- `{"type": "processing_start", "source", "ts"}`。
- `{"type": "processing_end", "source", "ts"}`。
- `{"type": "channel_message", "source": "channel", "channel", "sender", "content", "message_id", "timestamp", "ts", "history"?: bool}` — 重播连线前的旧消息时 `history` 为 `true`。
- `{"type": "error", "content", "source"?, "ts"}`。
- `{"type": "idle", "source"?, "ts"}`。

生命周期：

- 连线立刻接受；Terrarium不存在 → upgrade 前 `404`。
- 先重播频道历程。
- 之后即时流式事件。
- Client 关闭是 graceful；清理时会卸下输出并移除 callback。

### `WS /ws/creatures/{agent_id}`

独立代理的事件流。

送入 frame：`{"type": "input", "content": str|list[dict], "message"?: str}`。

送出 frame：跟Terrarium流一样的 `activity` / `text` / `processing_*` / `error` / `idle` 家族。第一个事件一定是 `{"type": "activity", "activity_type": "session_info", "source", "model", "agent_name", "ts"}`。

### `WS /ws/agents/{agent_id}/chat`

较单纯的 request-response chat 通道。

送入：`{"message": str}`。

送出：`{"type": "text", "content"}`、`{"type": "done"}`、`{"type": "error", "content"}`。

会跨多个回合保持开启。

### `WS /ws/terrariums/{terrarium_id}/channels`

Terrarium的只读频道 feed。

送出：`{"type": "channel_message", "channel", "sender", "content", "message_id", "timestamp"}`。

### `WS /ws/files/{agent_id}`

对代理工作目录做文件变动监看。

送出：

- `{"type": "ready", "root"}` — watcher 已启动。
- `{"type": "change", "changes": [{"path", "abs_path", "action": "added"|"modified"|"deleted"}]}` — 每秒批次一次。隐藏 / 被忽略的目录 (`.git`、`node_modules`、`__pycache__`、`.venv`、`.mypy_cache` 等) 会被过滤。
- `{"type": "error", "text"}`。

### `WS /ws/logs`

Server 程序 log 档的即时 tail。

送出：

- `{"type": "meta", "path", "pid"}` — 连上时送。
- `{"type": "line", "ts", "level", "module", "text"}` — 流式。
- `{"type": "error", "text"}`。

Server 会先重播最后 ~200 行，再开始流式新行。

### `WS /ws/terminal/{agent_id}`

代理工作目录下的互动式 PTY。

送入：

- `{"type": "input", "data": str}` — shell 输入 (要送出请在尾端加 `\n`)。
- `{"type": "resize", "rows": int, "cols": int}`。

送出：

- `{"type": "output", "data": str}` (UTF-8；不合法序列会被替换)。
- `{"type": "error", "data": str}`。

实作：

- Unix：`pty.openpty()` + fork + exec。
- Windows 配 `winpty`：ConPTY。
- Fallback：没有 PTY 的纯 pipe。
- 连上时送一个初始 `{"type": "output", "data": ""}`。
- 清理时：SIGTERM 然后 SIGKILL。

### `WS /ws/terminal/terrariums/{terrarium_id}/{target}`

跟每只代理的 terminal 一样，但在Terrarium里解析Creature名或 `"root"`。

---

## Schema

Request / response 用到的 Pydantic 模型。

### `TerrariumCreate`

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `config_path` | str | 是 | |
| `llm` | str \| None | 否 | |
| `pwd` | str \| None | 否 | |

### `TerrariumStatus`

| 字段 | 型别 | 必要 |
|---|---|---|
| `terrarium_id` | str | 是 |
| `name` | str | 是 |
| `running` | bool | 是 |
| `creatures` | dict | 是 |
| `channels` | list | 是 |

### `CreatureAdd`

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `name` | str | 是 | |
| `config_path` | str | 是 | |
| `listen_channels` | list[str] | 否 | `[]` |
| `send_channels` | list[str] | 否 | `[]` |

### `ChannelAdd`

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `name` | str | 是 | |
| `channel_type` | str | 否 | `"queue"` |
| `description` | str | 否 | `""` |

### `ChannelSend`

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `content` | `str \| list[ContentPartPayload]` | 是 | |
| `sender` | str | 否 | `"human"` |

### `WireChannel`

| 字段 | 型别 | 必要 |
|---|---|---|
| `channel` | str | 是 |
| `direction` | `"listen" \| "send"` | 是 |

### `AgentCreate`

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `config_path` | str | 是 | |
| `llm` | str \| None | 否 | |
| `pwd` | str \| None | 否 | |

### `AgentChat`

| 字段 | 型别 | 必要 |
|---|---|---|
| `message` | str \| None | 否 |
| `content` | list[ContentPartPayload] \| None | 否 |

`message` 或 `content` 至少给一个。

### `MessageEdit`

| 字段 | 型别 | 必要 |
|---|---|---|
| `content` | str | 是 |

### `SlashCommand`

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `command` | str | 是 | |
| `args` | str | 否 | `""` |

### `ModelSwitch`

| 字段 | 型别 | 必要 |
|---|---|---|
| `model` | str | 是 |

### `FileWrite`

| 字段 | 型别 | 必要 |
|---|---|---|
| `path` | str | 是 |
| `content` | str | 是 |

### `FileRename`

| 字段 | 型别 | 必要 |
|---|---|---|
| `old_path` | str | 是 |
| `new_path` | str | 是 |

### `FileDelete`

| 字段 | 型别 | 必要 |
|---|---|---|
| `path` | str | 是 |

### `FileMkdir`

| 字段 | 型别 | 必要 |
|---|---|---|
| `path` | str | 是 |

### Content parts

`ContentPartPayload` 是 `TextPartPayload`、`ImagePartPayload`、`FilePartPayload` 的 discriminated union。

 **`TextPartPayload`**

| 字段 | 型别 | 必要 |
|---|---|---|
| `type` | `"text"` | 是 |
| `text` | str | 是 |

 **`ImageUrlPayload`**

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `url` | str | 是 | |
| `detail` | `"auto" \| "low" \| "high"` | 否 | `"low"` |

 **`ContentMetaPayload`**

| 字段 | 型别 | 必要 |
|---|---|---|
| `source_type` | str \| None | 否 |
| `source_name` | str \| None | 否 |

 **`ImagePartPayload`**

| 字段 | 型别 | 必要 |
|---|---|---|
| `type` | `"image_url"` | 是 |
| `image_url` | ImageUrlPayload | 是 |
| `meta` | ContentMetaPayload \| None | 否 |

 **`FilePayload`**

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `path` | str \| None | 否 | |
| `name` | str \| None | 否 | |
| `content` | str \| None | 否 | |
| `mime` | str \| None | 否 | |
| `data_base64` | str \| None | 否 | |
| `encoding` | `"utf-8" \| "base64" \| None` | 否 | |
| `is_inline` | bool | 否 | `False` |

 **`FilePartPayload`**

| 字段 | 型别 | 必要 |
|---|---|---|
| `type` | `"file"` | 是 |
| `file` | FilePayload | 是 |

### `ScratchpadPatch`

| 字段 | 型别 | 必要 |
|---|---|---|
| `updates` | dict[str, str \| None] | 是 |

`null` 代表删掉该 key。

### `ApiKeyRequest`

| 字段 | 型别 | 必要 |
|---|---|---|
| `provider` | str | 是 |
| `key` | str | 是 |

### `ProfileRequest`

| 字段 | 型别 | 必要 | 默认 |
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

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `name` | str | 是 | |
| `backend_type` | str | 否 | `"openai"` |
| `base_url` | str | 否 | `""` |
| `api_key_env` | str | 否 | `""` |

### `DefaultModelRequest`

| 字段 | 型别 | 必要 |
|---|---|---|
| `name` | str | 是 |

### `UIPrefsUpdateRequest`

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `values` | dict[str, Any] | 否 | `{}` |

### `InstallRequest`

| 字段 | 型别 | 必要 |
|---|---|---|
| `url` | str | 是 |
| `name` | str \| None | 否 |

### `UninstallRequest`

| 字段 | 型别 | 必要 |
|---|---|---|
| `name` | str | 是 |

### `MCPServerRequest`

| 字段 | 型别 | 必要 | 默认 |
|---|---|---|---|
| `name` | str | 是 | |
| `transport` | str | 否 | `"stdio"` |
| `command` | str | 否 | `""` |
| `args` | list[str] | 否 | `[]` |
| `env` | dict[str, str] | 否 | `{}` |
| `url` | str | 否 | `""` |

---

## 事件型别

事件会持久化到 `SessionStore`，并通过 WebSocket 流式。每个事件都带 `type`、`source` (来源代理/Creature名称)、`ts` (Unix 秒)。

- `text` — 流式文本 chunk。
  - `content: str`。
- `activity` — 多种类型，以 `activity_type` 区分，例如 `session_info`、`tool_call`、`tool_result`、`token_usage`、`job_update`、`job_completed`、`model_switch`、`interrupt`、`regenerate`、`edit`、`rewind`、`promote`、`background_result`、`memory_compact`、`memory_search`、`memory_save`。
  - 其他字段视 `activity_type` 而定：`args`、`job_id`、`tools_used`、`result`、`output`、`turns`、`duration`、`task`、`trigger_id`、`event_type`、`channel`、`sender`、`content`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`cached_tokens`、`round`、`summary`、`messages_compacted`、`session_id`、`model`、`agent_name`、`max_context`、`compact_threshold`、`error_type`、`error`、`messages_cleared`、`background`、`subagent`、`tool`、`interrupted`、`final_state`。
- `processing_start`、`processing_end`。
- `user_input` — `content: str | list[dict]`。
- `channel_message` — `channel`、`sender`、`content`、`message_id`、`timestamp`。

## 会话存储

会话保存在 `~/.kohakuterrarium/sessions/`，扩展名为 `.kohakutr`（旧的 `.kt` 也支持）。数据表结构和 resume 路径请参见 [会话持久化概念](../concepts/impl-notes/session-persistence.md)。

## 给整合者的补充

- HTTP chat 端点是非流式的。要流式请用对应的 WebSocket。
- `/ws/terrariums/{id}` 与 `/ws/terrariums/{id}/channels` 连上时都会带频道历程；旧消息 frame 会带 `"history": true`。
- `/ws/files/{agent_id}` 需要代理有工作目录。
- Terminal client 在本地 terminal resize 时必须送 `resize` frame。

## 延伸阅读

- 概念：[会话持久化概念](../concepts/impl-notes/session-persistence.md)、[边界概念](../concepts/boundaries.md)。
- 指南：[编程方式使用指南](../guides/programmatic-usage.md)、[前端布局指南](../guides/frontend-layout.md)、[会话指南](../guides/sessions.md)。
- 参考：[CLI 参考](cli.md)、[Python API 参考](python.md)、[配置参考](configuration.md)。
