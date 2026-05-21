# `api.auth` — authentication / authorization at the API server layer

Four optional, independent gates that stack at the API server boundary:

| Layer | Gate | Where it lives |
|---|---|---|
| L1 | Host selection | frontend-only (`VITE_KT_BUNDLED` build flag) |
| L2 | Host token (bearer / WS sub-protocol) | `middleware.py` + `ws_auth.py` |
| L3 | Admin token (`X-Admin-Token`) | `dependencies.verify_admin_token` |
| L4 | User accounts (sqlite + bcrypt) | `db.py` / `users.py` / `sessions.py` / `tokens.py` / `routes.py` |

## Architectural invariant

**Auth is a horizontal concern at the API server layer.  It does not
leak into Studio, the engine, the terrarium runtime, the session
store, or any module below `src/kohakuterrarium/api/`.**

A request that reaches a handler may carry `request.state.user` (an
authenticated User dataclass) — but the handler hands that user's
identity off to `engine_pool.get_or_create(user.id)` and then operates
on the returned `Terrarium` exactly as the standalone code path
operates on its single global engine.  The engine itself never sees
the user.

This isolation has two payoffs:

1. **Single-tenant code stays single-tenant.**  No `user_id` parameters
   ever appear on engine / Studio / session-store methods.  The
   framework is a single-user library; the auth layer multiplexes it
   on the HTTP boundary.
2. **The CLI / TUI / `kt run` paths are auth-agnostic.**  They construct
   a `Terrarium` directly and use it.  Only the FastAPI server pulls
   in `api.auth`.

A unit-test guard enforces no `from kohakuterrarium.api.auth.*` imports
outside `src/kohakuterrarium/api/`: see
`tests/unit/api/auth/test_auth_module_isolation.py`.  One documented
carve-out (`cli/admin.py`) shares the TOML write path so the CLI and
admin-rotation API routes can't drift; any future cross-boundary
importer must be added to the allowlist with a written reason.

## Design docs

See `plans/1.5.0-roadmap/03-frontend-backend-connection/`:

- `README.md` — index + phases at a glance
- `design.md` — canonical design (capabilities, schema, engine pool, deployment shapes)
- `implementation-plan.md` — phase-by-phase work breakdown
