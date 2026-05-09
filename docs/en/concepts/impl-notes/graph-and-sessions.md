---
title: Graph and sessions
summary: How the terrarium computes connected components, mutates topology under mutation calls, and keeps the session store consistent across merges and splits.
tags:
  - concepts
  - impl-notes
  - graph
  - persistence
---

# Graph and sessions

## The problem this solves

A multi-creature terrarium is a graph that can change at runtime.
Three properties have to hold simultaneously:

1. **Reactive topology.** The runtime mental model — what shares an
   environment, what shares a session — has to follow connectivity
   automatically. If the user removes the last channel between two
   halves, those halves should become independent without operator
   intervention.
2. **Lossless history.** Merges and splits are informational
   transitions; a creature's prior conversation should still be
   recoverable from any descendant of the original graph.
3. **Live channels never reroute under their own messages.** When the
   environment changes (because of a merge or split), in-flight
   channel triggers must continue to deliver against the right live
   registry. Subscribers do not get notified to re-subscribe; the
   engine has to keep them pointed at the surviving objects.

A naïve approach (recompute graphs from scratch on every read, or
freeze the topology at start) breaks one of those three.

## Options considered

- **Eager full reindex on every mutation.** Recompute the whole graph
  table every time. Simple. O(N) per mutation, expensive observability
  (every change implies a full diff), and forces anyone who held a
  graph id to invalidate it.
- **Lazy: don't track components; ask "are A and B connected?" on
  demand.** Cheap mutations, expensive shared-environment lookup,
  hard to attach a session store to "a graph" if the graph doesn't
  exist as an object.
- **Pre-mutation prediction.** Compute the post-mutation components
  before applying the change, and reject changes that would create
  surprising states. Too restrictive; topology changes are user
  actions, not requests for permission.
- **Apply, then normalise.** Mutate the topology. After every change,
  recompute components only inside the affected graph and emit a
  `TopologyDelta` describing what happened. The engine reacts to the
  delta — re-allocate environments, copy session stores, repoint
  creatures, re-inject triggers, emit events. What we do.

## What we actually do

### Pure-data graph (`terrarium/topology.py`)

Topology lives in a plain `TopologyState` value:

- `state.graphs: dict[graph_id, GraphTopology]` — one entry per
  connected component. Each `GraphTopology` carries its creature
  ids, channel declarations, and bipartite listen / send edge maps.
- `state.creature_to_graph: dict[creature_id, graph_id]` — reverse
  index for "which graph does this creature live in?"

Mutations are pure functions: `add_creature`, `remove_creature`,
`add_channel`, `remove_channel`, `connect`, `disconnect`,
`set_listen`, `set_send`. Each returns a `TopologyDelta` describing
what changed (`kind` ∈ {`nothing`, `merge`, `split`}, plus
`old_graph_ids`, `new_graph_ids`, `affected_creatures`). No live
agents, no asyncio — the whole module is testable as plain data.

### Connected components (`find_components`, `_normalize_components`)

Connectivity is computed by BFS over a bipartite graph: creatures on
one side, channels on the other. Two creatures are in the same
component iff there is a path through channels they share (listen or
send). `find_components` rebuilds the per-creature ↔ per-channel
adjacency maps and runs BFS from each unvisited creature.

`_normalize_components` runs after a mutation that may have severed
connectivity. If the recomputed component count is `<= 1` the delta
is `kind="nothing"`. If it is greater, the largest component keeps
the original graph id and the rest get fresh ids; channels are
redistributed by which component they actually touch; the delta
reports `kind="split"` with the affected creatures.

### Merge bookkeeping (`channels.connect_creatures`, `_merge_environment_into`, `session_coord.apply_merge`)

When `Terrarium.connect(a, b, channel=...)` calls into the topology
layer and the result is `kind="merge"`, the engine:

1. Determines the surviving graph id and the dropped graph ids.
2. `_merge_environment_into(engine, surviving, dropped)` moves every
   channel object from the dropped environments into the surviving
   environment's `ChannelRegistry`. Every existing listen trigger is
   re-injected against the new env so its `on_send` callback keeps
   pointing at the right registry and the right session store.
3. Repoints every affected creature's `graph_id` to the surviving
   graph (so subsequent topology lookups land in the right place).
4. `session_coord.apply_merge(engine, delta)` consolidates the
   session stores. It calls `merge_session_stores(old_stores,
   new_path)` which creates a new store at the surviving graph's
   path, copies every event from each old store into the new store
   via `copy_events_into` (preserving `turn_index`, `spawned_in_turn`,
   `branch_id` and re-stamping event ids), and writes
   `parent_session_ids: [old_a, old_b]` plus `merged_at: <timestamp>`
   into the new store's meta.
5. Emits `TOPOLOGY_CHANGED(kind="merge", ...)`.

The new merged store immediately backs every channel persistence
callback in the surviving environment, because step 2 re-injected the
triggers (the callback closes over `engine.session_store_for(graph_id)`,
which now resolves to the merged store).

### Split bookkeeping (`channel_lifecycle.apply_split_bookkeeping`, `session_coord.apply_split`)

When a remove or disconnect call returns a delta with `kind="split"`,
the engine:

1. Allocates a fresh `Environment` per new component. Registers each
   new component's topology channels in its environment.
2. For every affected creature: repoints `creature.graph_id` to its
   new component, rebinds the creature's agent and executor to the
   new env, and tears down + re-injects every channel listen trigger
   against the new env's channel objects.
3. `session_coord.apply_split(engine, delta)` calls
   `split_session_store(old_store, new_paths)` which creates one new
   store per new component path and `copy_events_into` each child
   store with the full pre-split history. Each child store records
   `parent_session_ids: [old_graph_id]` and `split_at: <timestamp>`
   in its meta. The session store reverse map is updated so each new
   graph id resolves to its own store.
4. Emits `TOPOLOGY_CHANGED(kind="split", ...)`.

Pre-split history is preserved on every side. Branches diverge from a
shared root.

### Channel persistence callbacks (`channels._ensure_channel_persistence`)

When a channel is registered in a graph's environment that has a
session store attached, an `on_send` callback is installed on the
channel. The callback writes every send to the store via
`store.save_channel_message()`. The callback is idempotent (replacing
itself if installed again) and reads the current `_terrarium_graph_id`
on the channel object on every call — so when the channel object
moves home during a merge, the callback automatically targets the
surviving store without needing to be reinstalled.

This is what lets the merge path (step 2 above) move channel objects
without losing message persistence.

### Trigger re-injection (`channels.inject_channel_trigger`,
`_teardown_existing_trigger`)

`ChannelTrigger`s are async tasks subscribed to a specific channel
object. After a merge or split, the channel objects backing a
creature's listen edges may have changed identity (a different
`ChannelRegistry`, a different `Environment`). `inject_channel_trigger`
is idempotent: it tears down any existing trigger by id
(`channel_{subscriber_id}_{channel_name}`), unsubscribes from the old
channel, and resubscribes against the new live one. The
`apply_split_bookkeeping` and `_merge_environment_into` paths call
this for every affected creature × channel pair.

### Recipe as the source of truth on resume (`terrarium/resume.py`,
`terrarium/recipe.py`)

`resume_into_engine` does **not** persist the live topology to disk
and play it back. It loads `meta.config_path` (the recipe path
recorded when the session was created) and re-applies the recipe
against a fresh engine: declares channels, adds creatures, wires
listen / send edges, registers output wires, calls `assign_root_to`
if the recipe declared one. Per-creature saved state (conversation,
scratchpad, triggers) is then injected by `session.resume.resume_agent`
on each rebuilt creature.

A consequence: if the session was in a *split* state at save time,
resume rebuilds a single graph from the recipe. The lineage metadata
(`parent_session_ids`, `merged_at`, `split_at`) survives in the
resumed store, so the audit trail is still readable, but the live
topology returns to the recipe's natural shape.

## Invariants preserved

- **Connectivity ↔ graph id.** Two creatures are in the same graph if
  and only if they are connected through channels. Always.
- **One graph, one store.** A graph id maps to at most one
  `SessionStore`. Cross-graph creatures never share a store.
- **History is preserved on transition.** Merge unions histories into
  one new store; split duplicates the source store into each new
  store. No event is dropped, no event becomes unreadable.
- **Lineage is recorded.** Every transition writes `parent_session_ids`
  and a `merged_at` / `split_at` timestamp into the new store's meta.
- **Trigger objects always point at the live channel.** After any
  merge or split, every channel listen trigger has been torn down
  and re-injected against the surviving environment.
- **Recipe is canonical for shape.** Topology on resume comes from
  the recipe; the session contributes per-creature state and lineage
  metadata, not graph structure.

## Where it lives in the code

- `src/kohakuterrarium/terrarium/topology.py` — `TopologyState`,
  `GraphTopology`, `TopologyDelta`, pure mutation functions,
  `find_components`, `_normalize_components`, `_merge_graphs`.
- `src/kohakuterrarium/terrarium/engine.py` — `Terrarium`
  orchestrator. `add_creature`, `remove_creature`, `connect`,
  `disconnect`, `add_channel`, `remove_channel`, plus the
  environment / session-store registries.
- `src/kohakuterrarium/terrarium/channels.py` — `connect_creatures`,
  `_merge_environment_into`, `_ensure_channel_persistence`,
  `inject_channel_trigger`, `_teardown_existing_trigger`.
- `src/kohakuterrarium/terrarium/channel_lifecycle.py` —
  `apply_split_bookkeeping`, channel removal flow, environment
  reallocation.
- `src/kohakuterrarium/terrarium/session_coord.py` — `apply_merge`,
  `apply_split`, `merge_session_stores`, `split_session_store`,
  `copy_events_into`, meta refresh helpers.
- `src/kohakuterrarium/terrarium/runtime_prompt.py` — event-driven
  per-creature prompt refresh on `TOPOLOGY_CHANGED`,
  `CREATURE_STARTED`, `CREATURE_STOPPED`, `OUTPUT_WIRE_ADDED`,
  `OUTPUT_WIRE_REMOVED`, `PARENT_LINK_CHANGED`.
- `src/kohakuterrarium/terrarium/resume.py` — `resume_into_engine`,
  recipe-driven topology reconstruction.
- `src/kohakuterrarium/terrarium/events.py` — `EngineEvent` taxonomy,
  `EventFilter`.
- `src/kohakuterrarium/session/store.py` — `SessionStore` API used
  by the coordinator.

## See also

- [Dynamic graph](../multi-agent/dynamic-graph.md) — the user-facing
  mental model this implements.
- [Session persistence](session-persistence.md) — the underlying
  `.kohakutr` file format and per-creature resume.
- [Terrarium](../multi-agent/terrarium.md) — the engine contract.
