---
title: Dynamic graph
summary: Why the terrarium's graph mutates at runtime — connected components, auto-merge / auto-split, the in-graph "graph editor", and what session lineage costs and buys.
tags:
  - concepts
  - multi-agent
  - graph
  - hot-plug
---

# Dynamic graph

## What it is

A [terrarium](terrarium.md) is not a fixed shape. The set of creatures
running, the channels between them, and which creatures share a
session can all change at runtime — without restart, without
re-instantiating creatures that aren't affected, and without losing
history.

The engine models the live system as a **graph** of creatures
connected by channels. Each connected component of that graph is a
separate "graph id" with its own shared environment and its own
session store. When the topology changes, the engine reacts:

- A new creature is added → it lands in a fresh singleton component
  by default, or joins a specific component if the caller said so.
- A new channel wire crosses two components → they **auto-merge** into
  one component (and one merged session store).
- A creature or channel is removed → if connectivity is broken, the
  component **auto-splits** into the new connected pieces (and the
  session store is duplicated into each side).

All of this is structural work the engine performs deterministically
in response to mutation calls. The creatures inside the graph don't
make these decisions — the engine does.

## Why it exists

A static-topology multi-agent runtime can't express the things people
actually want to do at runtime:

- A privileged node decides mid-task that it needs a specialist it
  hadn't thought of, and wants to spawn one.
- Two independent sessions running side-by-side want to merge into a
  single conversation when one of them needs help from the other.
- A creature finishes its job and should be torn down without
  affecting anyone else; if it was a bridge between two halves, those
  halves should keep running independently.
- A team should be observable from outside without anyone in the team
  knowing they're being watched, and observation should track creature
  identity even as creatures come and go.

Making the graph dynamic — and giving the engine the bookkeeping
responsibility — lets all four work without per-recipe special
casing.

## The mental model

Each connected component is a **graph**. Two creatures are in the
same graph if and only if there is a path between them through
channels they share (listening or sending). The graph is the unit of:

- **Shared environment.** Channels declared in a graph live in that
  graph's `Environment`; only creatures in the graph see them.
- **Session.** One `.kohakutr` file backs one graph. Creatures in the
  same graph share history; creatures in different graphs do not.
- **Group tools.** Privileged operations (spawn, remove, channel CRUD,
  output-wire CRUD) act on the caller's graph.

Components are not declared. They are **derived** from the channel
adjacency at any given moment. A change in connectivity rederives
them.

## What can change at runtime

| Operation                       | Effect on topology               | Effect on session store              |
|---------------------------------|----------------------------------|--------------------------------------|
| `Terrarium.add_creature`        | New singleton component (default), or join named graph | Attaches when the graph has a store |
| `Terrarium.remove_creature`     | May split if the creature was a bridge | Split-side bookkeeping (duplicate) |
| `Terrarium.add_channel`         | No connectivity change           | None directly                        |
| `Terrarium.remove_channel`      | May split if the channel was the only path | Split-side bookkeeping       |
| `Terrarium.connect(a, b, ...)`  | If `a`, `b` in different graphs → merge | Merge stores; record `parent_session_ids` |
| `Terrarium.disconnect(a, b, ...)` | May split                      | Split-side bookkeeping               |

The same mutations are surfaced to a [privileged node](privileged-node.md)
inside the graph as the [group tools](../glossary.md#group-tools)
(`group_add_node`, `group_remove_node`, `group_start_node`,
`group_stop_node`, `group_channel`, `group_wire`). Together they act
as the in-graph **graph editor** — an LLM-driven privileged node can
evolve the team mid-run by calling tools, with every mutation
emitting an `EngineEvent` so observers and runtime prompts stay in
sync.

## Auto-merge

A merge happens when a connect crosses two graphs. The engine:

1. Unions the two graphs in the topology layer (creature ids, channel
   declarations, listen / send edges).
2. Unions the two `Environment`s — every channel object from the
   dropped graph moves into the surviving environment, and existing
   channel triggers are re-injected against the surviving env.
3. Merges the two session stores into one new store at the surviving
   graph's path. Every event from both old stores is copied into the
   new store; the new store records `parent_session_ids` for lineage
   and a `merged_at` timestamp.
4. Repoints every affected creature's `graph_id` to the surviving
   graph.
5. Emits a `TOPOLOGY_CHANGED` event with `kind="merge"`,
   `old_graph_ids`, `new_graph_ids`, and `affected_creatures`.

After the merge, traffic on any pre-existing channel from the dropped
graph routes through the merged environment, and session writes
target the merged store.

## Auto-split

A split happens when a removal severs the only connectivity path
between two halves of a graph. The engine:

1. Computes connected components on the post-mutation topology.
2. The largest component keeps the original graph id. Other
   components are minted fresh ids.
3. Allocates a fresh `Environment` per new component and registers
   that component's channels in it.
4. Re-injects every affected creature's channel triggers against the
   new env's channel objects (so messages flow on the correct live
   registry).
5. Duplicates the pre-split session store into each new component's
   path. Each child store inherits the full pre-split history and
   records `parent_session_ids` for lineage and a `split_at`
   timestamp.
6. Emits a `TOPOLOGY_CHANGED` event with `kind="split"` and the new
   graph ids.

History is never lost on a split — only duplicated. Branching
sessions diverge from the same starting point.

## Resume: the recipe is the source of truth

When a saved multi-creature session is resumed, the engine **rebuilds
the topology from the recipe**, not from a frozen snapshot of the
graph. The session metadata records `config_path`, `agents`, and
lineage (`parent_session_ids`, `merged_at`, `split_at`); the recipe
on disk is what the engine plays back to reconstruct creatures,
channels, and wiring before injecting the saved conversation.

This means:

- Editing the recipe between runs is a supported change. New channels
  appear, removed creatures are gone, output wiring is updated.
- A snapshot of a *split* state is **not** preserved. Resume
  reconstructs the recipe's natural shape; if the session was in a
  split state at save time, the resume rebuilds the original merged
  graph.
- Lineage metadata survives. Even though a resumed graph reuses the
  recipe's topology, you can still trace through `parent_session_ids`
  to find the histories that were merged or split into the current
  store.

## Privilege gate

Not every creature should be able to mutate the graph. The engine
distinguishes:

- **Privileged nodes** — the recipe `root:` node, recipe-declared
  members marked `privileged: true`, and creatures created with
  explicit `is_privileged=True`. They carry the
  [group tools](../glossary.md#group-tools).
- **Workers** — creatures spawned by `group_add_node` from a privileged
  caller. They land in the caller's graph but do not get the group
  tools. A worker cannot fork peers or graph edges without being
  promoted by the engine.

Privilege is a property of the runtime creature, not the underlying
agent config. The same config can run privileged in one terrarium and
unprivileged in another.

## Observability

Every topology mutation emits an `EngineEvent`:

- `CREATURE_STARTED` / `CREATURE_STOPPED`
- `OUTPUT_WIRE_ADDED` / `OUTPUT_WIRE_REMOVED`
- `PARENT_LINK_CHANGED`
- `TOPOLOGY_CHANGED` (merge / split / nothing, with old + new graph
  ids and affected creatures)
- `SESSION_FORKED` / `CREATURE_SESSION_ATTACHED`

A subscriber filters with an `EventFilter` over kinds, creature ids,
graph ids, and channels. The web dashboard uses this stream for live
panels; the runtime-prompt subscriber uses it to refresh affected
creatures' system prompts when the graph around them changes (so a
root's "graph awareness" block is always current).

## Don't be bounded

A static recipe with no runtime changes is the simplest mode and the
right default. Reach for hot-plug and group-tool authoring when the
work itself is dynamic — open-ended research where the team shape is
discovered as you go, ad-hoc rescue where one session pulls another
in, parallel exploration where branches split and merge.

## See also

- [Terrarium](terrarium.md) — the runtime engine the graph lives
  inside.
- [Privileged node](privileged-node.md) — the creature that carries
  the group tools; promoted via the `root:` recipe keyword or
  inline `privileged: true`.
- [impl-notes / graph and sessions](../impl-notes/graph-and-sessions.md)
  — how the merge / split bookkeeping is actually implemented.
- [reference / builtins — group_* tools](../../reference/builtins.md)
  — the group-tool surface.
