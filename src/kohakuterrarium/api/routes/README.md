# api/routes/

REST endpoint handlers, grouped by Studio namespace. Each module (or
subpackage) exports a `router: APIRouter` that `api/app.py` mounts under the
appropriate prefix.

## Files

| File / dir         | Responsibility                                                                          |
| ------------------ | ---------------------------------------------------------------------------------------- |
| `__init__.py`      | Package marker                                                                          |
| `catalog/`         | Package / registry / marketplace / config discovery, module + creature CRUD, workspace, templates, validation, server info |
| `identity/`        | LLM backends + profiles, API keys, Codex OAuth, MCP server registry, UI prefs, config files |
| `sessions_v2/`     | Engine-backed active sessions: per-creature chat / control / model / plugins / state, topology, wiring, memory search |
| `persistence/`     | Saved `.kohakutr` sessions: list / delete, resume into the engine, fork, history, viewer, artifacts, memory index |
| `attach/`          | Workspace files + attach policy hints                                                   |
| `app_update.py`    | App-update API for the Vue `Admin → Updates` tab                                        |
| `health.py`        | Liveness + readiness endpoints                                                          |
| `metrics.py`       | Process-wide metrics REST snapshot                                                      |
| `runtime_graph.py` | Runtime graph snapshot for the graph editor                                             |
| `nodes.py`, `lab_clients.py`, `lab_status.py` | Lab cluster: node discovery, per-client management, status snapshot |

## Dependency direction

Imported by `api/app.py` only. Imports: `fastapi`, `pydantic`; `studio/`
namespaces and the `Terrarium` engine (via `api/deps.get_engine`),
`session/` (resume, store, memory, embedding), `llm/` (profiles +
codex_auth), `packages/`, `core/config`, `terrarium/config`.

## Notes

- Every handler runs inside the FastAPI event loop; long-running work
  (agent turns, terrarium lifecycle) is delegated to the Studio
  namespaces and the engine; there is no separate service manager.
- `files`-style routes resolve paths against each agent's working
  directory and refuse requests that escape the root.
- Identity routes write to `~/.kohakuterrarium/` files using the same
  helpers `cli/config.py` uses, so CLI and web UI stay in sync.

## See also

- `../README.md`: api layer overview
- `../ws/README.md`: WebSocket counterparts (streaming chat, logs, file watcher)
- `../../studio/`: the management facade the routes delegate to
