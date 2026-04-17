# HTTP and WebSocket API

Every REST endpoint and WebSocket channel exposed by the in-package
FastAPI server (`kt web`, `kt serve`, `python -m kohakuterrarium.api.main`).
The API drives the Vue SPA and is suitable for any client that wants
to control agents and terrariums from outside the process.

For the shape of the serving layer and session storage, read
[concepts/impl-notes/session-persistence](../concepts/impl-notes/session-persistence.md).
For task-oriented use, see
[guides/programmatic-usage](../guides/programmatic-usage.md) and
[guides/frontend-layout](../guides/frontend-layout.md).

## Server configuration

- Default host: `0.0.0.0`.
- Default port: `8001` (auto-increments if busy under `kt web`).
- Override via `python -m kohakuterrarium.api.main --host 127.0.0.1 --port 8080 [--reload]`.
- `KT_SESSION_DIR` overrides the default session directory.
- CORS is wide open: `allow_origins=["*"]`, all methods, all headers.
- No authentication. Treat the server as trusted-local.
- Version string: `0.1.0`. No `/v1/` URL prefix.
- FastAPI auto-docs: `/docs` (Swagger UI), `/redoc` (ReDoc).

When `create_app(static_dir=Path)` is called with a valid built SPA
directory:

- `/assets/*` — hashed build assets.
- `/{path}` — SPA fallback, serves `index.html` for any unmatched path.
- `/api/*` and WebSocket routes take precedence.

## Response conventions

- Status codes: `200` success, `400` bad input, `404` missing resource,
  `500` server error. `201` is not used.
- Payloads are JSON unless otherwise noted.
- Errors use FastAPI `HTTPException` with `{"detail": "<message>"}`.

---

## Terrariums

### `POST /api/terrariums`

Create and start a terrarium from a config path.

- Body: `TerrariumCreate` (`config_path`, optional `llm`, `pwd`).
- Response: `{"terrarium_id": str, "status": "running"}`.
- Status: `200`, `400`.
- Side effects: terrarium spawned; root agent initialised; creatures
  started; session store opened when configured.

### `GET /api/terrariums`

List all running terrariums as an array of status objects (same shape
as the single-terrarium GET below).

### `GET /api/terrariums/{terrarium_id}`

Return a `TerrariumStatus`: `terrarium_id`, `name`, `running`,
`creatures` (name → status dict), `channels` (list of channel names).

### `DELETE /api/terrariums/{terrarium_id}`

Stop and clean up a terrarium. Response: `{"status": "stopped"}`. Side
effects: all creatures stopped, channels cleaned, session store closed.

### `POST /api/terrariums/{terrarium_id}/channels`

Add a channel at runtime.

- Body: `ChannelAdd` (`name`, `channel_type` default `"queue"`,
  `description`).
- Response: `{"status": "created", "channel": <name>}`.

### `GET /api/terrariums/{terrarium_id}/channels`

List channels as `[{"name", "type", "description"}]`.

### `POST /api/terrariums/{terrarium_id}/channels/{channel_name}/send`

Inject a message into a channel.

- Body: `ChannelSend` (`content` as `str` or `list[ContentPartPayload]`,
  `sender` default `"human"`).
- Response: `{"message_id": str, "status": "sent"}`.
- Side effects: message written to history; listeners fire their
  `on_send` callbacks.

### `POST /api/terrariums/{terrarium_id}/chat/{target}`

Non-streaming chat. `target` is `"root"` or a creature name.

- Body: `AgentChat` (`message` or `content`).
- Response: `{"response": <full text>}`.

### `GET /api/terrariums/{terrarium_id}/history/{target}`

Read conversation and event log. `target` is `"root"`, a creature name,
or `"ch:<channel_name>"` for channel history. Prefers SessionStore,
falls back to the in-memory log.

- Response: `{"terrarium_id", "target", "messages": [...], "events": [...]}`.

### `GET /api/terrariums/{terrarium_id}/scratchpad/{target}`

Return the target agent's scratchpad as `{key: value}`.

### `PATCH /api/terrariums/{terrarium_id}/scratchpad/{target}`

- Body: `ScratchpadPatch` (`updates: {key: value | null}`; `null`
  deletes).
- Response: updated scratchpad.

### `GET /api/terrariums/{terrarium_id}/triggers/{target}`

List active remote triggers:
`[{"trigger_id", "trigger_type", "running", "created_at"}]`.

### `GET /api/terrariums/{terrarium_id}/plugins/{target}`

List loaded plugins with enabled/disabled state.

### `POST /api/terrariums/{terrarium_id}/plugins/{target}/{plugin_name}/toggle`

Toggle a plugin. Response: `{"name", "enabled"}`. Calls
`load_pending()` when enabling.

### `GET /api/terrariums/{terrarium_id}/env/{target}`

Return `{"pwd", "env"}` with env keys containing `secret`, `key`,
`token`, `password`, `pass`, `private`, `auth`, `credential`
(case-insensitive) filtered out.

### `GET /api/terrariums/{terrarium_id}/system-prompt/{target}`

Return `{"text": <assembled system prompt>}`.

---

## Creatures (inside a terrarium)

### `GET /api/terrariums/{terrarium_id}/creatures`

Map of creature name to status dict.

### `POST /api/terrariums/{terrarium_id}/creatures`

Add a creature at runtime.

- Body: `CreatureAdd` (`name`, `config_path`, `listen_channels`,
  `send_channels`).
- Response: `{"creature": <name>, "status": "running"}`.

### `DELETE /api/terrariums/{terrarium_id}/creatures/{name}`

Remove a creature. Response: `{"status": "removed"}`.

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/interrupt`

Interrupt the creature's current `agent.process()` without terminating
it. Response: `{"status": "interrupted", "creature": <name>}`.

### `GET /api/terrariums/{terrarium_id}/creatures/{name}/jobs`

Running and queued background jobs.

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/tasks/{job_id}/stop`

Cancel a running background job. Response: `{"status": "cancelled", "job_id"}`.

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/promote/{job_id}`

Promote a direct task to the background queue.

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/model`

Switch the creature's LLM without restart.

- Body: `ModelSwitch` (`model`).
- Response: `{"status": "switched", "creature", "model"}`.

### `POST /api/terrariums/{terrarium_id}/creatures/{name}/wire`

Add a listen or send binding to a channel.

- Body: `WireChannel` (`channel`, `direction` = `"listen"` or `"send"`).
- Response: `{"status": "wired"}`.

---

## Standalone agents

### `POST /api/agents`

Create and start an agent outside of any terrarium.

- Body: `AgentCreate` (`config_path`, optional `llm`, `pwd`).
- Response: `{"agent_id", "status": "running"}`.

### `GET /api/agents`

List running agents.

### `GET /api/agents/{agent_id}`

Return `{"agent_id", "name", "model", "running"}`.

### `DELETE /api/agents/{agent_id}`

Stop the agent. Response: `{"status": "stopped"}`.

### `POST /api/agents/{agent_id}/interrupt`

Interrupt current processing.

### `POST /api/agents/{agent_id}/regenerate`

Re-run the last assistant response using current model/settings.
Response: `{"status": "regenerating"}`.

### `POST /api/agents/{agent_id}/messages/{msg_idx}/edit`

Mutate a user message and replay from that point.

- Body: `MessageEdit` (`content`).
- Response: `{"status": "edited"}`.
- Side effects: truncates history at `msg_idx`, injects new message,
  replays.

### `POST /api/agents/{agent_id}/messages/{msg_idx}/rewind`

Truncate the conversation without re-running. Response:
`{"status": "rewound"}`.

### `POST /api/agents/{agent_id}/promote/{job_id}`

Promote a direct task to the background.

### `GET /api/agents/{agent_id}/plugins`

List plugins and state.

### `POST /api/agents/{agent_id}/plugins/{plugin_name}/toggle`

Enable/disable a plugin. Response: `{"name", "enabled"}`.

### `GET /api/agents/{agent_id}/jobs`

List background jobs.

### `POST /api/agents/{agent_id}/tasks/{job_id}/stop`

Cancel a background job.

### `GET /api/agents/{agent_id}/history`

Return `{"agent_id", "events": [...]}`.

### `POST /api/agents/{agent_id}/model`

Switch the agent's LLM.

- Body: `ModelSwitch` (`model`).
- Response: `{"status": "switched", "model"}`.

### `POST /api/agents/{agent_id}/command`

Execute a user slash command (e.g. `model`, `status`).

- Body: `SlashCommand` (`command`, optional `args`).
- Response: command-dependent result.

### `POST /api/agents/{agent_id}/chat`

Non-streaming chat.

- Body: `AgentChat`.
- Response: `{"response": <full text>}`.

### `GET /api/agents/{agent_id}/scratchpad`

Return scratchpad key-value map.

### `PATCH /api/agents/{agent_id}/scratchpad`

- Body: `ScratchpadPatch`.
- Response: updated scratchpad.

### `GET /api/agents/{agent_id}/triggers`

Active triggers as `[{trigger_id, trigger_type, running, created_at}]`.

### `GET /api/agents/{agent_id}/env`

Return `{"pwd", "env"}` with secrets filtered.

### `GET /api/agents/{agent_id}/system-prompt`

Return `{"text": <system prompt>}`.

---

## Config discovery

### `GET /api/configs/creatures`

List discoverable creature configs:
`[{"name", "path", "description"}]`. Paths may be absolute or package
references.

### `GET /api/configs/terrariums`

List discoverable terrarium configs (same shape as above).

### `GET /api/configs/server-info`

Return `{"cwd", "platform"}`.

### `GET /api/configs/models`

List every configured LLM model/profile with availability.

### `GET /api/configs/commands`

List slash commands: `[{"name", "aliases", "description", "layer"}]`.

---

## Registry and package management

### `GET /api/registry`

Scan local directories and installed packages. Return
`[{"name", "type", "description", "model", "tools", "path", "source", ...}]`.
`source` is `"local"` or a package name.

### `GET /api/registry/remote`

Return `{"repos": [...]}` from the bundled `registry.json`.

### `POST /api/registry/install`

- Body: `InstallRequest` (`url`, optional `name`).
- Response: `{"status": "installed", "name"}`.

### `POST /api/registry/uninstall`

- Body: `UninstallRequest` (`name`).
- Response: `{"status": "uninstalled", "name"}`.

---

## Sessions

### `GET /api/sessions`

List saved sessions.

Query params:

| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `20` | Max sessions. |
| `offset` | int | `0` | Skip N. |
| `search` | str | — | Filter by name, config, agents, preview (case-insensitive). |
| `refresh` | bool | `false` | Force rebuild of the session index. |

Response:

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

Side effects: the index is rebuilt on first request or after 30 seconds.

### `DELETE /api/sessions/{session_name}`

Delete a session file. Response: `{"status": "deleted", "name"}`. Accepts
stem or full filename.

### `POST /api/sessions/{session_name}/resume`

Resume a saved session.

- Response: `{"instance_id", "type": "agent"|"terrarium", "session_name"}`.
- Status codes: `200`, `400` (ambiguous prefix), `404`, `500`.

### `GET /api/sessions/{session_name}/history`

Session metadata and available targets.

- Response: `{"session_name", "meta", "targets"}` where targets contain
  agent names, `"root"`, and `"ch:<channel>"` entries.

### `GET /api/sessions/{session_name}/history/{target}`

Read-only saved history. `target` is URL-encoded; accepts `"root"`,
creature name, or `"ch:<channel_name>"`.

- Response: `{"session_name", "target", "meta", "messages", "events"}`.

### `GET /api/sessions/{session_name}/memory/search`

FTS5 / semantic / hybrid search over a saved session.

Query params:

| Param | Type | Default | Description |
|---|---|---|---|
| `q` | str | required | Query. |
| `mode` | `auto\|fts\|semantic\|hybrid` | `auto` | Search mode. |
| `k` | int | `10` | Max results. |
| `agent` | str | — | Filter by agent. |

Response: `{"session_name", "query", "mode", "k", "count", "results"}`.
Each result: `{content, round, block, agent, block_type, score, ts, tool_name, channel}`.

Side effects: unindexed events get indexed (idempotent); uses the live
embedder when the agent is running, otherwise loads from config.

---

## Files

### `GET /api/files/tree`

Nested file tree.

Query params: `root` (required), `depth` (default `3`, clamped `1..10`).

Response: recursive object
`{"name", "path", "type": "directory"|"file", "children": [...], "size"}`.

### `GET /api/files/browse`

Directory-browse view for filesystem UI.

Query params: `path` (optional).

Response:
`{"current": {...}, "parent": str|null, "roots": [...], "directories": [...]}`.

### `GET /api/files/read`

Read a text file.

- Query params: `path` (required).
- Response: `{"path", "content", "size", "modified", "language"}`.
- Errors: binary files, permission denied → `400`; missing → `404`.

### `POST /api/files/write`

- Body: `FileWrite` (`path`, `content`).
- Response: `{"success": true, "size"}`.
- Side effects: parent directories created.

### `POST /api/files/rename`

- Body: `FileRename` (`old_path`, `new_path`).
- Response: `{"success": true}`.

### `POST /api/files/delete`

Delete a file or empty directory.

- Body: `FileDelete` (`path`).
- Response: `{"success": true}`.

### `POST /api/files/mkdir`

Recursive mkdir.

- Body: `FileMkdir` (`path`).
- Response: `{"success": true}`.

---

## Settings and configuration

### API keys

#### `GET /api/settings/keys`

Return `{"providers": [{"provider", "backend_type", "env_var", "has_key", "masked_key", "available", "built_in"}]}`.

#### `POST /api/settings/keys`

- Body: `ApiKeyRequest` (`provider`, `key`).
- Response: `{"status": "saved", "provider"}`.

#### `DELETE /api/settings/keys/{provider}`

Response: `{"status": "removed", "provider"}`.

### Codex

#### `POST /api/settings/codex-login`

Run the Codex OAuth flow server-side (server must be local). Response:
`{"status": "ok", "expires_at"}`.

#### `GET /api/settings/codex-status`

Return `{"authenticated", "expired"?}`.

#### `GET /api/settings/codex-usage`

Fetch Codex usage for the past 14 days. Status: `200`, `401` (token
refresh failed), `404` (no login).

### Backends

#### `GET /api/settings/backends`

`{"backends": [{"name", "backend_type", "base_url", "api_key_env", "built_in", "has_token", "available"}]}`.

#### `POST /api/settings/backends`

- Body: `BackendRequest` (`name`, `backend_type` default `"openai"`,
  `base_url`, `api_key_env`).
- Response: `{"status": "saved", "name"}`.

#### `DELETE /api/settings/backends/{name}`

Response: `{"status": "deleted", "name"}`. Built-in backends cannot be
deleted (`400`).

### Profiles

#### `GET /api/settings/profiles`

`{"profiles": [...]}` with fields `name, model, provider, backend_type, base_url, api_key_env, max_context, max_output, temperature, reasoning_effort, service_tier, extra_body`.

#### `POST /api/settings/profiles`

- Body: `ProfileRequest`.
- Response: `{"status": "saved", "name"}`.

#### `DELETE /api/settings/profiles/{name}`

Response: `{"status": "deleted", "name"}`.

#### `GET /api/settings/default-model`

`{"default_model"}`.

#### `POST /api/settings/default-model`

- Body: `DefaultModelRequest` (`name`).
- Response: `{"status": "set", "default_model"}`.

#### `GET /api/settings/models`

Same as `GET /api/configs/models`.

### UI prefs

#### `GET /api/settings/ui-prefs`

`{"values": {...}}`.

#### `POST /api/settings/ui-prefs`

- Body: `UIPrefsUpdateRequest` (`values`).
- Response: `{"values": <merged>}`.

### MCP

#### `GET /api/settings/mcp`

`{"servers": [{"name", "transport", "command", "args", "env", "url"}]}`.

#### `POST /api/settings/mcp`

- Body: `MCPServerRequest`.
- Response: `{"status": "saved", "name"}`.

#### `DELETE /api/settings/mcp/{name}`

Response: `{"status": "removed", "name"}`.

---

## WebSocket endpoints

All WebSocket endpoints are bidirectional over a standard upgrade (no
custom headers or subprotocols). Clients receive a stream of JSON
frames and may send input frames. The server closes on error; there
is no auto-reconnect or heartbeat — the client is responsible.

### `WS /ws/terrariums/{terrarium_id}`

Unified event stream for an entire terrarium (root + creatures +
channels).

Inbound frames:

- `{"type": "input", "target": "root"|<creature>, "content": str|list[dict], "message"?: str}` —
  queues input for the target. Server acknowledges with
  `{"type": "idle", "source": <target>, "ts": float}`.
- Other message types are ignored.

Outbound frames:

- `{"type": "activity", "activity_type": ..., "source", "ts", ...}` —
  activity types include `session_info`, `tool_call`, `tool_result`,
  `token_usage`, `job_update`, `job_completed`, and more (see
  [Event types](#event-types)).
- `{"type": "text", "content", "source", "ts"}` — streaming text chunk.
- `{"type": "processing_start", "source", "ts"}`.
- `{"type": "processing_end", "source", "ts"}`.
- `{"type": "channel_message", "source": "channel", "channel", "sender", "content", "message_id", "timestamp", "ts", "history"?: bool}` —
  `history` is `true` for the replay of messages that pre-date the
  connection.
- `{"type": "error", "content", "source"?, "ts"}`.
- `{"type": "idle", "source"?, "ts"}`.

Lifecycle:

- Connection accepted immediately; terrarium missing → `404` before
  upgrade.
- Channel history is replayed first.
- Events stream in real time.
- Client close is graceful; cleanup detaches outputs and removes
  callbacks.

### `WS /ws/creatures/{agent_id}`

Event stream for a standalone agent.

Inbound frames: `{"type": "input", "content": str|list[dict], "message"?: str}`.

Outbound frames: same `activity` / `text` / `processing_*` / `error` /
`idle` families as the terrarium stream. The first event is always
`{"type": "activity", "activity_type": "session_info", "source", "model", "agent_name", "ts"}`.

### `WS /ws/agents/{agent_id}/chat`

Simpler request-response chat channel.

Inbound: `{"message": str}`.

Outbound: `{"type": "text", "content"}`, `{"type": "done"}`,
`{"type": "error", "content"}`.

Stays open across multiple turns.

### `WS /ws/terrariums/{terrarium_id}/channels`

Read-only channel feed for a terrarium.

Outbound: `{"type": "channel_message", "channel", "sender", "content", "message_id", "timestamp"}`.

### `WS /ws/files/{agent_id}`

File-change watch on an agent's working directory.

Outbound:

- `{"type": "ready", "root"}` — watcher started.
- `{"type": "change", "changes": [{"path", "abs_path", "action": "added"|"modified"|"deleted"}]}` —
  batched every 1 second. Hidden/ignored directories (`.git`,
  `node_modules`, `__pycache__`, `.venv`, `.mypy_cache`, …) are
  filtered.
- `{"type": "error", "text"}`.

### `WS /ws/logs`

Live tail of the server process's log file.

Outbound:

- `{"type": "meta", "path", "pid"}` — sent on connect.
- `{"type": "line", "ts", "level", "module", "text"}` — streamed.
- `{"type": "error", "text"}`.

The server first replays the last ~200 lines, then streams new ones.

### `WS /ws/terminal/{agent_id}`

Interactive PTY inside the agent's working directory.

Inbound:

- `{"type": "input", "data": str}` — shell input (include `\n` to submit).
- `{"type": "resize", "rows": int, "cols": int}`.

Outbound:

- `{"type": "output", "data": str}` (UTF-8; invalid sequences replaced).
- `{"type": "error", "data": str}`.

Implementation:

- Unix: `pty.openpty()` + fork + exec.
- Windows with `winpty`: ConPTY.
- Fallback: plain pipes without PTY.
- Initial `{"type": "output", "data": ""}` sent on connect.
- On cleanup: SIGTERM then SIGKILL.

### `WS /ws/terminal/terrariums/{terrarium_id}/{target}`

Same as the per-agent terminal, but resolves the creature or `"root"`
inside a terrarium.

---

## Schemas

Pydantic models used in request and response bodies.

### `TerrariumCreate`

| Field | Type | Required | Default |
|---|---|---|---|
| `config_path` | str | yes | |
| `llm` | str \| None | no | |
| `pwd` | str \| None | no | |

### `TerrariumStatus`

| Field | Type | Required |
|---|---|---|
| `terrarium_id` | str | yes |
| `name` | str | yes |
| `running` | bool | yes |
| `creatures` | dict | yes |
| `channels` | list | yes |

### `CreatureAdd`

| Field | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes | |
| `config_path` | str | yes | |
| `listen_channels` | list[str] | no | `[]` |
| `send_channels` | list[str] | no | `[]` |

### `ChannelAdd`

| Field | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes | |
| `channel_type` | str | no | `"queue"` |
| `description` | str | no | `""` |

### `ChannelSend`

| Field | Type | Required | Default |
|---|---|---|---|
| `content` | `str \| list[ContentPartPayload]` | yes | |
| `sender` | str | no | `"human"` |

### `WireChannel`

| Field | Type | Required |
|---|---|---|
| `channel` | str | yes |
| `direction` | `"listen" \| "send"` | yes |

### `AgentCreate`

| Field | Type | Required | Default |
|---|---|---|---|
| `config_path` | str | yes | |
| `llm` | str \| None | no | |
| `pwd` | str \| None | no | |

### `AgentChat`

| Field | Type | Required |
|---|---|---|
| `message` | str \| None | no |
| `content` | list[ContentPartPayload] \| None | no |

At least one of `message` or `content` must be provided.

### `MessageEdit`

| Field | Type | Required |
|---|---|---|
| `content` | str | yes |

### `SlashCommand`

| Field | Type | Required | Default |
|---|---|---|---|
| `command` | str | yes | |
| `args` | str | no | `""` |

### `ModelSwitch`

| Field | Type | Required |
|---|---|---|
| `model` | str | yes |

### `FileWrite`

| Field | Type | Required |
|---|---|---|
| `path` | str | yes |
| `content` | str | yes |

### `FileRename`

| Field | Type | Required |
|---|---|---|
| `old_path` | str | yes |
| `new_path` | str | yes |

### `FileDelete`

| Field | Type | Required |
|---|---|---|
| `path` | str | yes |

### `FileMkdir`

| Field | Type | Required |
|---|---|---|
| `path` | str | yes |

### Content parts

`ContentPartPayload` is a discriminated union of `TextPartPayload`,
`ImagePartPayload`, and `FilePartPayload`.

**`TextPartPayload`**

| Field | Type | Required |
|---|---|---|
| `type` | `"text"` | yes |
| `text` | str | yes |

**`ImageUrlPayload`**

| Field | Type | Required | Default |
|---|---|---|---|
| `url` | str | yes | |
| `detail` | `"auto" \| "low" \| "high"` | no | `"low"` |

**`ContentMetaPayload`**

| Field | Type | Required |
|---|---|---|
| `source_type` | str \| None | no |
| `source_name` | str \| None | no |

**`ImagePartPayload`**

| Field | Type | Required |
|---|---|---|
| `type` | `"image_url"` | yes |
| `image_url` | ImageUrlPayload | yes |
| `meta` | ContentMetaPayload \| None | no |

**`FilePayload`**

| Field | Type | Required | Default |
|---|---|---|---|
| `path` | str \| None | no | |
| `name` | str \| None | no | |
| `content` | str \| None | no | |
| `mime` | str \| None | no | |
| `data_base64` | str \| None | no | |
| `encoding` | `"utf-8" \| "base64" \| None` | no | |
| `is_inline` | bool | no | `False` |

**`FilePartPayload`**

| Field | Type | Required |
|---|---|---|
| `type` | `"file"` | yes |
| `file` | FilePayload | yes |

### `ScratchpadPatch`

| Field | Type | Required |
|---|---|---|
| `updates` | dict[str, str \| None] | yes |

`null` values delete the key.

### `ApiKeyRequest`

| Field | Type | Required |
|---|---|---|
| `provider` | str | yes |
| `key` | str | yes |

### `ProfileRequest`

| Field | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes | |
| `model` | str | yes | |
| `provider` | str | no | `""` |
| `max_context` | int | no | `128000` |
| `max_output` | int | no | `16384` |
| `temperature` | float \| None | no | |
| `reasoning_effort` | str | no | `""` |
| `service_tier` | str | no | `""` |
| `extra_body` | dict \| None | no | |

### `BackendRequest`

| Field | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes | |
| `backend_type` | str | no | `"openai"` |
| `base_url` | str | no | `""` |
| `api_key_env` | str | no | `""` |

### `DefaultModelRequest`

| Field | Type | Required |
|---|---|---|
| `name` | str | yes |

### `UIPrefsUpdateRequest`

| Field | Type | Required | Default |
|---|---|---|---|
| `values` | dict[str, Any] | no | `{}` |

### `InstallRequest`

| Field | Type | Required |
|---|---|---|
| `url` | str | yes |
| `name` | str \| None | no |

### `UninstallRequest`

| Field | Type | Required |
|---|---|---|
| `name` | str | yes |

### `MCPServerRequest`

| Field | Type | Required | Default |
|---|---|---|---|
| `name` | str | yes | |
| `transport` | str | no | `"stdio"` |
| `command` | str | no | `""` |
| `args` | list[str] | no | `[]` |
| `env` | dict[str, str] | no | `{}` |
| `url` | str | no | `""` |

---

## Event types

Events are persisted to `SessionStore` and streamed over WebSockets.
Every event carries `type`, `source` (originating agent/creature name),
and `ts` (Unix seconds).

- `text` — streaming text chunk.
  - `content: str`.
- `activity` — diverse type discriminated by `activity_type`, e.g.
  `session_info`, `tool_call`, `tool_result`, `token_usage`,
  `job_update`, `job_completed`, `model_switch`, `interrupt`,
  `regenerate`, `edit`, `rewind`, `promote`, `background_result`,
  `memory_compact`, `memory_search`, `memory_save`.
  - Additional fields depend on `activity_type`: `args`, `job_id`,
    `tools_used`, `result`, `output`, `turns`, `duration`, `task`,
    `trigger_id`, `event_type`, `channel`, `sender`, `content`,
    `prompt_tokens`, `completion_tokens`, `total_tokens`,
    `cached_tokens`, `round`, `summary`, `messages_compacted`,
    `session_id`, `model`, `agent_name`, `max_context`,
    `compact_threshold`, `error_type`, `error`, `messages_cleared`,
    `background`, `subagent`, `tool`, `interrupted`, `final_state`.
- `processing_start`, `processing_end`.
- `user_input` — `content: str | list[dict]`.
- `channel_message` — `channel`, `sender`, `content`, `message_id`,
  `timestamp`.

## Session storage

Sessions live in `~/.kohakuterrarium/sessions/` with extension
`.kohakutr` (legacy `.kt` still accepted). See
[concepts/impl-notes/session-persistence](../concepts/impl-notes/session-persistence.md)
for the table layout and resume path.

## Notes for integrators

- HTTP chat endpoints are non-streaming. For streaming, use the
  matching WebSocket.
- Channel history is included on WS connect for both
  `/ws/terrariums/{id}` and `/ws/terrariums/{id}/channels`; historical
  frames carry `"history": true`.
- `/ws/files/{agent_id}` requires the agent to have a working
  directory.
- Terminal clients must send a `resize` frame whenever the local
  terminal is resized.

## See also

- Concepts:
  [session persistence](../concepts/impl-notes/session-persistence.md),
  [boundaries](../concepts/boundaries.md).
- Guides:
  [programmatic usage](../guides/programmatic-usage.md),
  [frontend layout](../guides/frontend-layout.md),
  [sessions](../guides/sessions.md).
- Reference: [cli](cli.md), [python](python.md),
  [configuration](configuration.md).
