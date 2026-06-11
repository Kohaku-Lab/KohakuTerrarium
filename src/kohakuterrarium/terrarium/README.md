# terrarium

Runtime engine for creature graphs.

A `Terrarium` is the no-LLM, no-decision runtime that hosts running creatures.
A solo agent is a one-creature graph; a team is a connected graph wired by
channels and output wiring. Intelligence lives in creatures. Management concerns
above the engine (catalog, identity, active sessions, persistence, attach,
editors) live in `studio/`.

The legacy `TerrariumRuntime` / `KohakuManager` / `terrarium_*` tool stack has
been removed; the `Terrarium` engine, the `Creature` handle from
`creature_host.py`, and the `group_*` tool surface are the only paths.

## Files

| File                                                   | Responsibility                                                                                                 |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| `engine.py`                                            | `Terrarium` engine: graph/session lifecycle, add/remove creatures, connect/disconnect, observe, output wiring. |
| `creature_host.py`                                     | `Creature` handle around a running `Agent`; exposes `chat()`, `inject_input()`, status, graph metadata.        |
| `creature.py`, `creature_ops.py`                       | `CreatureHandle` dataclass + pure agent-touching helpers shared with `studio.sessions`.                        |
| `topology.py`, `topology_snapshot.py`                  | Pure-data graph/channel topology model, merge/split deltas, runtime snapshot + replay.                         |
| `events.py`                                            | `EngineEvent`, `EventKind`, `EventFilter` observable event model.                                              |
| `autosession.py`, `session_coord.py`                   | Engine-owned session stores (`session_dir` autosession) and merge/split coordination.                          |
| `recipe.py`, `resume.py`                               | Apply terrarium recipes into the engine; adopt a saved session into a live engine.                             |
| `channels.py`, `channel_lifecycle.py`                  | Channel layer + disconnect/split lifecycle helpers.                                                            |
| `wiring.py`, `output_wiring.py`                        | Deterministic turn-output routing (engine helpers + resolver).                                                 |
| `root.py`, `runtime_prompt.py`                         | `assign_root` privileged-node promotion; group-prompt sync into creature system prompts.                       |
| `tools_group*.py`                                      | The `group_*` tool surface (lifecycle / channel / send / status / wire + shared helpers, hooks, caller context). |
| `service.py`, `remote_service.py`, `multi_node_*.py`   | `TerrariumService` runtime abstraction, Lab-backed remote variant, multi-node cluster/routing/replication.     |
| `wire.py`                                              | Pack/unpack helpers for terrarium DTOs sent over Laboratory APP.                                               |
| `observer.py`, `output_log.py`                         | Non-destructive channel observation and per-creature output capture.                                           |
| `config.py`                                            | Terrarium config loading + topology prompt.                                                                    |
| `engine_cli.py`, `engine_rich_cli.py`, `cli_output.py` | Engine TUI / rich inline-CLI launchers and headless CLI output.                                                |

## Dependency direction

- Imports: `core/`, `bootstrap/`, `builtins/`, `modules/`, `session/`, `utils/`.
- Imported by: `studio/`, `api/`, `cli/`, `compose/`, and the group tools.
- One-way dependency: `terrarium/` may depend on `core/`; `core/` must never depend on `terrarium/`.

## Key entry points

```python
from kohakuterrarium import Terrarium

engine, creature = await Terrarium.with_creature("@kt-biome/creatures/swe")
async for chunk in creature.chat("Explain this project"):
    print(chunk, end="")
await engine.shutdown()
```

- `await Terrarium.with_creature(config)`: create an engine and one running creature.
- `await Terrarium.from_recipe(recipe)`: create an engine and apply a multi-creature recipe.
- `await engine.add_creature(config, graph=None, start=True)`: add a creature to an existing or new graph.
- `await engine.connect(a, b, channel=...)` / `disconnect(...)`: wire or unwire graph edges; may merge/split graphs.
- `await engine.wire_output(creature, sink)`: deterministic turn-output routing.
- `engine.subscribe(EventFilter(...))`: observe text chunks, channel messages, topology changes, lifecycle, errors, and session forks.
- `await engine.shutdown()`: stop all creatures.

Use `Studio` when you also need package catalog, settings/identity, saved-session
persistence, attach policies, or editor workflows.

## Notes

- A Terrarium has no LLM of its own. It routes and hosts; creatures reason.
- A privileged node is a normal creature hosted by the same engine, promoted
  via `assign_root` (or `is_privileged=True` at add time); it carries the
  `group_*` tools and, with root-style wiring, is user-facing.
- Channels provide optional/conditional traffic and are broadcast-only.
  Output wiring provides deterministic pipeline edges.
- Graph topology is pure data; live changes emit `TOPOLOGY_CHANGED`, `SESSION_FORKED`, and related engine events.

## See also

- `../studio/README.md`: management facade above the engine.
- `../core/README.md`: `Agent` + channel primitives.
- `docs/en/concepts/multi-agent/terrarium.md`: runtime mental model.
- `docs/en/guides/programmatic-usage.md`: embedding examples.
