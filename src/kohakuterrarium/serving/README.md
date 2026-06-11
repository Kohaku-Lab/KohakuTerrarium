# serving

Web/desktop launch helpers plus the process-metrics aggregator.

The runtime path is:

- `Terrarium` (`kohakuterrarium.terrarium`) owns the live creature graph.
- `Studio` (`kohakuterrarium.studio`) owns catalog, identity, active sessions,
  saved-session persistence, attach policy, and editor workflows.
- `api/`, `cli/`, the web dashboard, and the desktop app delegate those concerns
  to Studio/Terrarium; there is no separate service manager. The old
  `AgentSession` / `KohakuManager` wrappers have been deleted; use
  `Agent.build`, `Creature.chat()`, or Studio session modules instead.

`serving/` remains for launch glue only.

## Files

| File                 | Description                                                                                        |
| -------------------- | -------------------------------------------------------------------------------------------------- |
| `web.py`             | Static frontend / FastAPI launcher used by `kt web`, `kt serve`, and `kt app`.                     |
| `process_metrics.py` | Process-wide metrics aggregator: the canonical subscriber to the metrics hook.                    |
| `events.py`          | Legacy serving event dataclasses for older transport-facing code.                                  |
| `__init__.py`        | Empty public export surface; new code should import `Studio` / `Terrarium` from `kohakuterrarium`. |

## Dependency notes

- `serving.web` may import the API app and frontend path helpers to launch local
  web/desktop surfaces.
- New session-management code belongs in `studio/`; new graph-runtime code
  belongs in `terrarium/`.
