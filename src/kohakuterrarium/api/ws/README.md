# api/ws/

WebSocket handlers for streaming events from the framework to connected
clients.

## Files

| File | Route | Responsibility |
|------|-------|----------------|
| `__init__.py` | — | Package marker |
| `chat.py` | `/ws/terrariums/{tid}`, `/ws/creatures/{agent_id}` | **Primary unified stream** — all events (text chunks, tool activity, channel messages, tokens, triggers) tagged by source for one mount point |
| `agents.py` | `/ws/agents/{agent_id}/chat` | Legacy bidirectional chat with a standalone agent (simpler, single-agent) |
| `channels.py` | `/ws/terrariums/{tid}/channels` | Stream ONLY channel messages for a terrarium |
| `files.py` | `/ws/agents/{agent_id}/files` | Watch the agent's working directory (via `watchfiles`) and push change events |
| `logs.py` | `/ws/logs` | Tail the API server's own log file (parsed into `{ts, level, module, text}`) |
| `terminal.py` | `/ws/agents/{agent_id}/terminal` | PTY bridge — spawn a shell in the agent's working dir, stream stdin/stdout both ways |

## Dependency direction

Imported by `api/app.py` only. Imports: `fastapi.WebSocket`, `api/deps`,
`api/events` (`StreamOutput`, `get_event_log`), `llm/message` (content part
normalization), `utils/logging`. `terminal.py` optionally imports `winpty`
on Windows and `pty` / `termios` / `fcntl` elsewhere.

## Key entry points

- **`chat.py`** — start here. The unified stream endpoint attaches a
  `StreamOutput` (from `api/events`) as a secondary output on the target
  agent/terrarium, so every event the agent produces also flows through
  a `asyncio.Queue` that the WebSocket drains. Late-joining clients
  receive a replay from the per-mount event log.
- `channels.py` — simpler, channel-only stream for UIs that only care
  about cross-creature traffic.
- `files.py:_watch_directory` — push diff events for filesystem changes.
- `terminal.py:_pty_session` — full PTY bridge with resize + signal handling.

## Notes

- All endpoints handle `WebSocketDisconnect` gracefully: the server
  detaches its `StreamOutput` secondary and the agent keeps running.
- `chat.py` is the only handler that accepts client→server messages
  beyond the initial handshake (chat input, slash commands, message
  edits). Everything else is server→client only.
- `terminal.py` degrades on Windows: if `winpty` is installed it uses a
  real ConPTY; otherwise the route returns a friendly error.
- Protocols are stable JSON frames with a `type` field — see the
  docstring at the top of each file for the exact schema.

## See also

- `../events.py` — `StreamOutput` and shared event-log store
- `../routes/README.md` — REST counterparts for non-streaming operations
- `plans/inventory-runtime.md` §13 — serving layer + streaming
