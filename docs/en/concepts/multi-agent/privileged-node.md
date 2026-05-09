---
title: Privileged node
summary: A creature in a graph with group tools registered — the `root:` recipe keyword promotes one node to this state.
tags:
  - concepts
  - multi-agent
  - privileged
  - root
---

# Privileged node

## What it is

A **privileged node** is a creature inside a [graph](../glossary.md#graph)
that has been granted the [group tools](../glossary.md#group-tools)
needed to mutate the graph it belongs to: spawn or remove other
creatures, draw or delete channels, start or stop members, query
graph status. Structurally it is just another creature — same config,
same modules, same lifecycle. What makes it "privileged" is the
runtime flag (`creature.is_privileged = True`) and the corresponding
tool registration the engine performs at promotion time.

`root:` in a terrarium recipe is one specific way to mark a node as
privileged. Recipes can also mark members privileged inline; engine
APIs accept a `privileged=True` flag at creature-add time. Tool-
spawned worker creatures (via `group_add_node`) are *not* privileged
by default — workers can't fork peers without explicit elevation.

## Why it exists

Two needs share the same answer:

1. **A graph that can edit itself.** Multi-agent work often discovers
   the team shape mid-task. Some node has to be allowed to call
   `group_add_node`, `group_channel`, etc. The flag identifies which
   ones.
2. **A user-facing surface.** When a human is interacting with a
   graph, they need a single counterparty to talk to. That node
   typically wants the same authority — see what's happening, spawn
   helpers, rewire channels — so it makes sense for the user-facing
   node to also be privileged.

The `root:` recipe keyword captures the second case as a one-line
shorthand: declare a privileged node and apply the standard
"user-facing root" wiring (a `report_to_root` channel everyone reports
into, root listening on every other channel in the graph). The
underlying mechanism is the privilege flag plus the wiring; "root" is
just the convention.

## How we define it

Three ways to make a node privileged:

### 1. The `root:` recipe keyword

```yaml
terrarium:
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md     # team-specific delegation prompt
    controller:
      reasoning_effort: high
  creatures:
    - ...
```

The recipe loader builds the node, marks it privileged, opens (or
reuses) a `report_to_root` channel, makes every other creature wired
to send on it, makes the node listen on every other channel in the
graph, and force-registers the group tools. It is also mounted as the
user-facing surface (TUI / CLI / web tab).

### 2. Inline `privileged: true` on a recipe member

```yaml
terrarium:
  creatures:
    - name: planner
      base_config: "@kt-biome/creatures/general"
      privileged: true
      ...
```

Used when you want a privileged member that isn't the user-facing
surface — for example, a privileged "supervisor" sitting alongside
several workers, with a separate user-facing root.

### 3. Imperative promotion

```python
async with Terrarium() as engine:
    sup = await engine.add_creature(
        "@kt-biome/creatures/general",
        is_privileged=True,
    )
    # sup carries group tools immediately

# or, for the full root-style wiring (report_to_root + listen-all):
from kohakuterrarium.terrarium.root import assign_root_to
assign_root_to(engine, sup)
```

`engine.add_creature(..., is_privileged=True)` is the minimal
promotion: the flag is set and `force_register_privileged_tools` runs.
`assign_root_to(engine, creature)` is the full root-style helper —
privilege plus the `report_to_root` channel plus listen-all wiring.

## How we implement it

- **Privilege flag:** `Creature.is_privileged` — a runtime property of
  the creature handle, not the underlying agent config.
- **Tool registration:** `terrarium/tools_group.py` exposes
  `force_register_basic_tools` (always) and
  `force_register_privileged_tools` (only on privileged nodes). The
  privileged surface is `group_add_node`, `group_remove_node`,
  `group_start_node`, `group_stop_node`, `group_channel`, `group_wire`,
  `group_status`.
- **Recipe `root:`:** the recipe loader calls `assign_root_to` after
  the node is built. `terrarium/root.py:assign_root_to` ensures the
  `report_to_root` channel exists, wires every other creature in the
  graph to send on it, makes the privileged node listen on every
  pre-existing channel, marks it privileged, and registers the
  privileged tool surface.
- **Topology refresh:** the runtime-prompt subscriber listens for
  `TOPOLOGY_CHANGED` events and regenerates the "graph awareness"
  block on every creature affected by the change — so a privileged
  node's prompt always reflects the current creatures, channels, and
  wiring.

## What you can therefore do

- **User-facing conductor.** The user asks the privileged node "have
  the SWE fix the auth bug, then have the reviewer approve it." The
  node sends messages through channels and watches `report_to_root`
  for completion.
- **Dynamic team construction.** A privileged node calls
  `group_add_node` to spawn specialists, `group_channel` to declare
  channels, `group_wire` to add output-wiring edges, and
  `group_remove_node` / `group_stop_node` to wind members down.
- **Cross-graph rewiring.** `group_channel` with a target outside the
  caller's graph routes through `Terrarium.connect`, which merges the
  two graphs (and their session stores) so the caller can take
  ownership of a previously-independent creature.
- **Multiple privileged nodes per graph.** Nothing requires there to
  be exactly one. A graph can carry a user-facing root plus a
  privileged supervisor, or several supervisors splitting the team.
- **Observability pivot.** A root-style privileged node auto-listens
  on every channel and receives `report_to_root` traffic — it's a
  natural place to run summarisation plugins, alerting rules, etc.

## Don't be bounded

Graphs without any privileged node are perfectly valid — think
headless pipelines, cron-driven coordination, batch jobs. Privilege
is a convenience for runtime authoring; if your team shape is fixed
by the recipe, you may never need it.

## See also

- [Terrarium](terrarium.md) — the engine the graph and its
  privileged nodes live inside.
- [Dynamic graph](dynamic-graph.md) — how the group tools mutate
  topology and how the engine reacts.
- [Multi-agent overview](README.md) — where privileged nodes fit in
  the model.
- [reference/builtins.md — group_* tools](../../reference/builtins.md)
  — the privileged toolset.
