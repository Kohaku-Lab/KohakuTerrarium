# terrarium

The runtime engine for creature graphs.

A `Terrarium` hosts every running creature in the process. It runs no LLM and
has no reasoning loop: intelligence lives in the creatures it hosts. What it
owns is **structure**: which creatures share a connected component (a graph),
which channels exist between them, where each turn-end output is delivered,
which session store backs which graph, and the bookkeeping that follows when
the topology changes. A solo agent is a one-creature graph; a team is a
connected graph wired by channels and output wiring.

Management concerns above the engine (catalog, identity, active sessions,
persistence, attach, editors) live in `studio/`, which drives the engine
through the `TerrariumService` abstraction in `service/`.

## Mental model

- **Graph**: a connected component of creatures linked by channels. Adding a
  creature joins a graph (default: a fresh singleton). Connecting across
  graphs auto-merges them; disconnecting or removing a bridge auto-splits.
  Each merge or split coordinates the session stores and records lineage.
- **Creature**: a live `Agent` plus engine-side metadata (`graph_id`,
  `is_privileged`, `listen_channels`, `send_channels`, `parent_creature_id`).
- **Channels** are broadcast-only: every listener receives every send.
  **Output wiring** provides deterministic pipeline edges for turn-end output.
- **Privileged node**: a normal creature promoted with `assign_root` (or
  `is_privileged=True` at add time). It carries the `group_*` mutation tools
  and, with root-style wiring, is user-facing.
- **Drive**: a durable per-graph work resource delivered to creatures; see
  `drive/README.md`.
- Topology is pure data (`graph/topology.py`). Live changes go through topology
  pure functions, then environment updates, then session coordination, and
  emit `EngineEvent`s. The engine bus carries structure events only:
  `TOPOLOGY_CHANGED`, `CHANNEL_MESSAGE`, creature lifecycle
  (`CREATURE_ADDED` / `STARTED` / `STOPPED`), output-wire and parent-link
  changes, and `DRIVE_*` events. Per-creature content (text chunks, tool
  activity, errors) flows through the typed turn surface instead
  (`Creature.run_stream` / `Creature.attach`).

## Layout

Families are packages with the family prefix dropped; unit tests mirror the
tree under `tests/unit/terrarium/`.

```
terrarium/
├── __init__.py            lazy public facade (Terrarium, Creature, services, events, ...)
├── engine/                the engine
│   ├── engine.py          Terrarium: graph and session lifecycle, creatures, channels, wiring
│   ├── creature_add.py    creature construction and insertion
│   ├── observability.py   event subscription and status
│   └── cli/               engine launchers: tui, rich, commands, tabs, output
├── creature/              the per-creature handle
│   ├── host.py            Creature: chat(), inject_input(), status, graph metadata, build_creature
│   ├── handle.py          CreatureHandle (config-level wrapper)
│   ├── ids.py             creature-id mint / clean / decode
│   ├── ops.py             pure agent-touching helpers shared with studio.sessions
│   └── host_resources.py  host-resources flag read from inside a creature
├── graph/                 graph model
│   ├── topology.py        pure-data graph / channel model, merge and split deltas
│   ├── snapshot.py        runtime snapshot and replay (+ leftovers.py)
│   ├── manifest.py        versioned reconstruction manifests (+ checkpoint.py)
│   └── identity.py        graph-local creature identity (+ identity_engine.py guards)
├── channels/              channel layer (channels.py) and disconnect / split lifecycle
├── session/               engine-owned session stores
│   ├── autosession.py     session_dir autosession: one store per graph
│   ├── coord.py           merge / split coordination, lineage metadata
│   └── event_copy.py  successor.py  unload.py  observers.py
├── resume/                adopt a saved session into a live engine
│   └── resume.py  manifest.py  workspace.py
├── recipe/                apply a TerrariumConfig recipe
│   └── loader.py  apply.py  transaction.py  identity.py  discard.py  remote.py
├── tools/                 the group_* tool surface
│   ├── group.py           registration (communication tools for all, mutation tools by privilege)
│   └── lifecycle.py  channel.py  send.py  status.py  wire.py  common.py  hooks.py  context.py
├── service/               TerrariumService abstraction
│   ├── service.py         the Protocol and LocalTerrariumService
│   ├── remote.py          RemoteTerrariumService (Lab APP calls)
│   └── dto.py  wire.py  history.py  history_remote.py  subagent.py  local_command.py  command_inventory.py
├── multi_node/            MultiNodeTerrariumService: one local engine plus N remote workers
│   └── service.py  cluster.py  routing.py  replication.py  channels.py  runtime_options.py
├── wiring/                deterministic turn-output routing (wiring.py, output.py resolver)
├── drive/                 the Drive runtime resource
├── events.py              EngineEvent, EventKind, EventFilter
├── root.py                assign_root privileged-node promotion
├── runtime_prompt.py      keeps each creature's prompt in sync with its group
├── observer.py            non-destructive channel observation
├── output_log.py          per-creature output capture
└── config.py              terrarium config loading and topology prompt
```

## The service layer

`TerrariumService` (in `service/service.py`) captures every operation Studio
performs on a runtime. Three implementations:

- `LocalTerrariumService`: direct in-process calls on a `Terrarium`.
- `RemoteTerrariumService`: a Lab APP-backed proxy with the same surface.
- `MultiNodeTerrariumService`: a composite for `lab-host` mode, with a
  `creature_id -> home_node` registry that routes per-creature operations and
  fans out global reads.

## The group tool surface

Registered on every creature: `send_channel`, `group_send`. Registered only on
privileged nodes: `group_status`, `group_add_node`, `group_spawn_child`,
`group_remove_node`, `group_start_node`, `group_stop_node`, `group_pause_node`,
`group_resume_node`, `group_kill_node`, `group_channel`, `group_wire`. Workers
spawned by `group_add_node` are not privileged.

## Key entry points

```python
from kohakuterrarium import Terrarium

engine, creature = await Terrarium.with_creature("@kt-biome/creatures/swe")
async for chunk in creature.chat("Explain this project"):
    print(chunk, end="")
await engine.shutdown()
```

- `Terrarium(session_dir=...)`: turns on autosession (one store per graph);
  without it, persistence is opt-in per call.
- `await Terrarium.with_creature(config)`: an engine plus one running creature.
- `await Terrarium.from_recipe(recipe)`: an engine plus a multi-creature recipe.
- `await Terrarium.resume(...)` / `engine.adopt_session(...)`: restore a saved session.
- `await engine.add_creature(config, graph=None, start=True)`: add to an existing or new graph.
- `await engine.connect(a, b, channel=...)` / `disconnect(...)`: wire or unwire graph edges; may merge or split graphs.
- `await engine.wire_output(creature, target)`: deterministic turn-output routing.
- `await engine.assign_root(...)`: promote a privileged node.
- `engine.subscribe(EventFilter(...))`: observe structure events (topology changes, channel messages, creature lifecycle, output wiring, Drive).
- `await engine.wake(creature)`: start a stopped creature; user input calls it so a message reactivates a stopped creature.
- `await engine.shutdown()`: stop all creatures.

Use `Studio` when you also need the package catalog, settings and identity,
saved-session persistence, attach policies, or editor workflows.

## Dependency direction

- Imports: `core/`, `bootstrap/`, `builtins/`, `modules/`, `session/`, `utils/`.
- Imported by: `studio/`, `api/`, `cli/`, `compose/`, `laboratory/`, and the group tools.
- `terrarium/` may depend on `core/`; `core/` must never depend on `terrarium/`.

## See also

- `drive/README.md`: the Drive resource.
- `../studio/README.md`: the management facade above the engine.
- `../core/README.md`: `Agent` and channel primitives.
- `docs/en/concepts/multi-agent/terrarium.md`: the runtime mental model.
- `docs/en/guides/programmatic-usage.md`: embedding examples.
