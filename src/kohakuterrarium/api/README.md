# api/

FastAPI HTTP + WebSocket server for KohakuTerrarium.

## Responsibility

Exposes the :class:`Terrarium` engine and the studio sessions modules
over HTTP / WebSocket so web frontends, desktop apps, and automation
tools can drive creatures without importing the Python package. Thin
translation only — all state lives in `terrarium/` (the engine) and
the studio session caches, shared across requests via a singleton
engine.

## Files

| File          | Responsibility                                                                                                                |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py` | Package marker                                                                                                                |
| `app.py`      | `create_app(creatures_dirs, terrariums_dirs, static_dir)` — FastAPI factory + CORS + router registration + optional SPA mount |
| `main.py`     | Uvicorn entrypoint (`python -m kohakuterrarium.api.main`), default port 8001                                                  |
| `deps.py`     | `get_engine()` — singleton `Terrarium` dependency                                                                             |
| `schemas.py`  | Pydantic request/response models (`TerrariumCreate`, `AgentChat`, `ChannelSend`, `FileWrite`, ...)                            |
| `routes/`     | REST endpoints (one file per resource); see `routes/README.md`                                                                |
| `ws/`         | WebSocket handlers for streaming events; see `ws/README.md`                                                                   |

## Dependency direction

Imported by: `cli/serve.py` (to launch the server), `serving/web.py`
(to embed alongside the SPA), `api/main.py` (uvicorn entrypoint).

Imports: `fastapi`, `pydantic`, `uvicorn`; `terrarium/` (engine),
`studio/` (sessions, identity, persistence, attach, catalog),
`llm/` (profiles + codex auth for `settings` routes), `packages/`,
`core/config`, `utils/logging`.

Nothing inside `core/`, `bootstrap/`, `builtins/`, or `terrarium/`
imports from `api/`.

## Key entry points

- `create_app(...)` — build and configure a FastAPI instance
- `get_engine()` — dependency-injected singleton `Terrarium`

## Notes

- All REST routes are mounted under `/api/*`. WebSocket routes live at
  `/ws/*` so they don't collide with the SPA catch-all.
- When `static_dir` is supplied to `create_app`, a catch-all `GET
/{full_path:path}` serves the Vue SPA's `index.html` (real files under
  `static_dir/assets/` are served first, so hashed bundles win).
- The engine singleton is created on the first `get_engine()` call and
  shut down via FastAPI lifespan — `main.py` uses uvicorn's default
  lifespan integration, so `engine.shutdown()` runs on SIGTERM.
- `deps.py` reads `KT_SESSION_DIR` (default `~/.kohakuterrarium/sessions`)
  to choose where the engine stores `.kohakutr` files.

## See also

- `../terrarium/README.md` — the runtime engine this API wraps
- `routes/README.md` — REST endpoint map
- `ws/README.md` — WebSocket event stream protocol
