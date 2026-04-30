# terrarium

Runtime engine for creature graphs.

A `Terrarium` is the no-LLM, no-decision runtime that hosts running creatures.
A solo agent is a one-creature graph; a team is a connected graph wired by
channels and output wiring. Intelligence lives in creatures. Management concerns
above the engine (catalog, identity, active sessions, persistence, attach,
editors) live in `studio/`.

The older `TerrariumRuntime` stack is still present for compatibility and some
legacy CLI paths, but new code should prefer the `Terrarium` engine and the
`Creature` handle from `creature_host.py`.

## Files

| File | Responsibility |
|------|----------------|
| `engine.py` | `Terrarium` engine: graph/session lifecycle, add/remove creatures, connect/disconnect, observe, output wiring. |
| `creature_host.py` | `Creature` handle around a running `Agent`; exposes `chat()`, `inject_input()`, status, graph metadata. |
| `topology.py` | Pure-data graph/channel topology model and merge/split deltas. |
| `events.py` | `EngineEvent`, `EventKind`, `EventFilter` observable event model. |
| `sessions.py`, `session_coord.py` | Engine-backed session stores and merge/split coordination. |
| `recipe_loader.py`, `recipe_apply.py` | Load/apply terrarium recipes into the engine. |
| `channels.py`, `output_wiring.py` | Channel trigger injection and deterministic turn-output routing. |
| `runtime.py`, `api.py`, `factory.py`, `hotplug.py` | Legacy `TerrariumRuntime` / `TerrariumAPI` compatibility stack. |
| `cli.py`, `legacy_resume.py` | CLI drivers and legacy terrarium resume path. |
| `tool_manager.py`, `tool_registration.py` | Shared state and lazy registration for terrarium management tools. |
| `observer.py`, `output_log.py`, `persistence.py` | Legacy observer/output/session helpers. |

## Dependency direction

- Imports: `core/`, `bootstrap/`, `builtins/`, `modules/`, `session/`, `utils/`.
- Imported by: `studio/`, `api/`, `cli/`, compatibility `serving/`, and terrarium tools.
- One-way dependency: `terrarium/` may depend on `core/`; `core/` must never depend on `terrarium/`.

## Key entry points

```python
from kohakuterrarium import Terrarium

engine, creature = await Terrarium.with_creature("@kt-biome/creatures/swe")
async for chunk in creature.chat("Explain this project"):
    print(chunk, end="")
await engine.shutdown()
```

- `await Terrarium.with_creature(config)` — create an engine and one running creature.
- `await Terrarium.from_recipe(recipe)` — create an engine and apply a multi-creature recipe.
- `await engine.add_creature(config, graph=None, start=True)` — add a creature to an existing or new graph.
- `await engine.connect(a, b, channel=...)` / `disconnect(...)` — wire or unwire graph edges; may merge/split graphs.
- `await engine.wire_output(creature, sink)` — deterministic turn-output routing.
- `engine.subscribe(EventFilter(...))` — observe text chunks, channel messages, topology changes, lifecycle, errors, and session forks.
- `await engine.shutdown()` — stop all creatures.

Use `Studio` when you also need package catalog, settings/identity, saved-session
persistence, attach policies, or editor workflows.

## Notes

- A Terrarium has no LLM of its own. It routes and hosts; creatures reason.
- An optional root creature is a normal creature hosted by the same engine and marked as root with `assign_root`; conceptually it is user-facing and outside the worker-team role.
- Channels provide optional/conditional traffic. Output wiring provides deterministic pipeline edges.
- Graph topology is pure data; live changes emit `TOPOLOGY_CHANGED`, `SESSION_FORKED`, and related engine events.
- Legacy `TerrariumRuntime` docs remain only for compatibility code. Avoid building new surfaces on it.

## See also

- `../studio/README.md` — management facade above the engine.
- `../core/README.md` — `Agent` + channel primitives.
- `docs/en/concepts/multi-agent/terrarium.md` — runtime mental model.
- `docs/en/guides/programmatic-usage.md` — embedding examples.
