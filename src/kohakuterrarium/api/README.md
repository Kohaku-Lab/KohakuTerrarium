# api/

FastAPI HTTP + WebSocket server for KohakuTerrarium.

## Responsibility

Exposes the `serving/` layer (`KohakuManager`, `AgentSession`) over HTTP and
WebSocket so web frontends, desktop apps, and automation tools can drive
agents and terrariums without importing the Python package. Thin translation
only — all state lives in `serving/` and is shared across requests via a
singleton manager.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Package marker |
| `app.py` | `create_app(creatures_dirs, terrariums_dirs, static_dir)` — FastAPI factory + CORS + router registration + optional SPA mount |
| `main.py` | Uvicorn entrypoint (`python -m kohakuterrarium.api.main`), default port 8001 |
| `deps.py` | `get_manager()` — singleton `KohakuManager` dependency |
| `schemas.py` | Pydantic request/response models (`TerrariumCreate`, `AgentChat`, `ChannelSend`, `FileWrite`, ...) |
| `events.py` | Shared in-memory event log + `StreamOutput` (OutputModule that tees events onto a queue) |
| `routes/` | REST endpoints (one file per resource); see `routes/README.md` |
| `ws/` | WebSocket handlers for streaming events; see `ws/README.md` |

## Dependency direction

Imported by: `cli/serve.py` (to launch the server), `serving/web.py` (to
embed alongside the SPA), `api/main.py` (uvicorn entrypoint).

Imports: `fastapi`, `pydantic`, `uvicorn`; `serving/` (KohakuManager),
`session/` (resume, memory, store, embedding), `llm/` (profiles + codex
auth for `settings` routes), `packages.py`, `terrarium/config`,
`core/config`, `utils/logging`.

Nothing inside `core/`, `bootstrap/`, `builtins/`, or `terrarium/` imports
from `api/`.

## Key entry points

- `create_app(...)` — build and configure a FastAPI instance
- `get_manager()` — dependency-injected singleton `KohakuManager`
- `StreamOutput(source, queue, log)` — secondary `OutputModule` used by
  WebSocket handlers to tag and fan events out to connected clients
- `get_event_log(key)` — per-mount in-memory ring for replay to late-joining
  WebSocket clients

## Notes

- All REST routes are mounted under `/api/*`. WebSocket routes live at
  `/ws/*` so they don't collide with the SPA catch-all.
- When `static_dir` is supplied to `create_app`, a catch-all `GET
  /{full_path:path}` serves the Vue SPA's `index.html` (real files under
  `static_dir/assets/` are served first, so hashed bundles win).
- The manager singleton is created on the first `get_manager()` call and
  shut down via FastAPI lifespan — `main.py` uses uvicorn's default
  lifespan integration, so `manager.shutdown()` runs on SIGTERM.
- `deps.py` reads `KT_SESSION_DIR` (default `~/.kohakuterrarium/sessions`)
  to choose where the manager stores `.kohakutr` files.

## See also

- `../serving/README.md` — the lifecycle layer this API wraps
- `routes/README.md` — REST endpoint map
- `ws/README.md` — WebSocket event stream protocol
- `plans/inventory-runtime.md` §13 — serving layer flow
