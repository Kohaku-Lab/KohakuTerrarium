# serving

Web/desktop launch helpers plus legacy compatibility wrappers.

The v1.3 runtime path is:

- `Terrarium` (`kohakuterrarium.terrarium`) owns the live creature graph.
- `Studio` (`kohakuterrarium.studio`) owns catalog, identity, active sessions,
  saved-session persistence, attach policy, and editor workflows.
- `api/`, `cli/`, the web dashboard, and the desktop app delegate those concerns
  to Studio/Terrarium instead of using a separate service manager.

`serving/` remains for launch glue and backwards-compatible imports while older
embedding code migrates.

## Files

| File | Description |
|------|-------------|
| `web.py` | Static frontend / FastAPI launcher used by `kt web`, `kt serve`, and `kt app`. |
| `agent_session.py` | Legacy `AgentSession` compatibility wrapper over `Agent`; prefer `Creature.chat()` or `Studio.sessions.chat`. |
| `manager.py` | Legacy `KohakuManager` compatibility facade; prefer `Studio` + `Terrarium`. |
| `events.py` | Compatibility event dataclasses for older transport-facing code. |
| `__init__.py` | Empty public export surface; new code should import `Studio` / `Terrarium` from `kohakuterrarium`. |

## Dependency notes

- `serving.web` may import the API app and frontend path helpers to launch local
  web/desktop surfaces.
- Compatibility wrappers depend on `core.Agent` and legacy `TerrariumRuntime`.
  They are not the architecture path for new route handlers.
- New session-management code belongs in `studio/`; new graph-runtime code
  belongs in `terrarium/`.
