# api/routes/

REST endpoint handlers. One file per resource. Each module exports a
`router: APIRouter` that `api/app.py` mounts under the appropriate prefix.

## Files

| File | Prefix | Responsibility |
|------|--------|----------------|
| `__init__.py` | — | Package marker |
| `terrariums.py` | `/api/terrariums` | Terrarium CRUD + lifecycle + chat; scratchpad patch for individual creatures |
| `creatures.py` | `/api/terrariums/{tid}/creatures` | List / add / remove / wire creatures; model switch |
| `channels.py` | `/api/terrariums/{tid}/channels` | List channels, send a message to a channel |
| `agents.py` | `/api/agents` | Standalone agent lifecycle + chat + slash commands + env redaction helper (`_redacted_env`) |
| `configs.py` | `/api/configs` | Config discovery (scan creature + terrarium directories, list LLM profiles, list builtin user commands) |
| `files.py` | `/api/files` | File tree browse / read / write / rename / delete / mkdir for the editor panel |
| `registry.py` | `/api/registry` | Browse bundled `registry.json`, install / uninstall packages via git |
| `sessions.py` | `/api/sessions` | List saved `.kohakutr` sessions, resume, search memory |
| `settings.py` | `/api/settings` | LLM profiles + backends, API key storage, Codex OAuth, MCP server config |

## Dependency direction

Imported by `api/app.py` only. Imports: `fastapi`, `pydantic`; `serving/`
(via `api/deps.get_manager`), `session/` (resume, store, memory, embedding),
`llm/` (profiles + codex_auth), `packages.py`, `core/config`,
`terrarium/config`, `builtins/user_commands` (just for discovery).

## Key entry points

Each file's `router` symbol is the entry point. Shared helpers:

- `agents._redacted_env()` — scrub secrets from an env dump; reused by `terrariums.py`
- `configs.set_config_dirs(creatures, terrariums)` — wired once at startup by `create_app`
- `settings._load_mcp_config` / `_save_mcp_config` — reused by `cli/config_mcp.py`

## Notes

- Every handler runs inside the FastAPI event loop; long-running work
  (agent turns, terrarium lifecycle) is delegated to `KohakuManager`
  which owns its own task group.
- `configs.py` scans directories lazily the first time each endpoint is
  hit; callers trigger a rescan by re-calling `set_config_dirs`.
- `files.py` resolves paths against each agent's working directory and
  refuses requests that escape the root — enforced with
  `Path.resolve().is_relative_to(root)`.
- `settings.py` writes to `~/.kohakuterrarium/` files (`profiles.yaml`,
  `api_keys.json`, `mcp.json`, `codex_tokens.json`) using the same helpers
  `cli/config.py` uses, so CLI and web UI stay in sync.

## See also

- `../README.md` — api layer overview
- `../ws/README.md` — WebSocket counterparts (streaming chat, logs, file watcher)
- `../../serving/manager.py` — where the actual work happens
