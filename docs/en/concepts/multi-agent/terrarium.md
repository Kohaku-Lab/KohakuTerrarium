---
title: Terrarium
summary: The runtime engine for solo and multi-creature graphs — channels, output wiring, hot-plug, sessions, and observability.
tags:
  - concepts
  - multi-agent
  - terrarium
---

# Terrarium

## What it is

A **terrarium** is the runtime engine that hosts every running creature
in the process. It has no LLM of its own, no intelligence, and no
decisions. There is one engine per process; multiple disconnected
graphs may coexist inside it.

A standalone agent is a **1-creature graph** in the engine. A
multi-agent team is a **connected graph** wired by channels. The
config file you used to call a "terrarium" is now a **recipe** — a
sequence of "add these creatures, declare these channels, wire these
edges" that the engine executes. The engine itself is always present;
the recipe just populates it.

The engine does:

1. **Creature CRUD** — add, remove, list, inspect.
2. **Channel CRUD** — declare, connect creatures, disconnect.
3. **Output wiring** — turn-end events into named targets.
4. **Lifecycle** — start, stop, shutdown.
5. **Session merge / split** when topology changes graph membership.
6. **Observability** — `EngineEvent` stream for everything observable.

That is the entire contract.

### Mental model: one team, one root

The picture you start with — and the one most users keep — is a single
team behind a user-facing root creature:

```
  +---------+       +---------------------------+
  |  User   |<----->|        Root Agent         |
  +---------+       |  (terrarium tools, TUI)   |
                    +---------------------------+
                          |               ^
            sends tasks   |               |  observes
                          v               |
                    +---------------------------+
                    |     Terrarium Layer       |
                    |   (pure wiring, no LLM)   |
                    +-------+----------+--------+
                    |  swe  | reviewer |  ....  |
                    +-------+----------+--------+
```

This is the **per-graph view**: a root creature on top, a connected
graph of peers below, the terrarium-as-wiring in between. It's what
the framework natively provides and what most recipes encode. If this
is all you need, stop here — the rest of this section is engine
internals you can reach for when you outgrow the single-team picture.

### Engine-wide view: the runtime that hosts every graph

The engine is a process-wide host. One per process. Inside it, any
number of graphs may coexist — your team, an ad-hoc solo agent you
spun up for a quick chat, a monitor creature with no peers — each as
its own connected component. Topology is not frozen; channels can be
drawn between graphs at runtime, which merges them, and channels can
be removed, which may split a graph back apart.

```
              +-----------------------------------------+
              |              Terrarium engine           |
              |          (one per process, no LLM)      |
              +-----------------------------------------+
                |                  |                |
         graph A             graph B           graph C
   +-------------------+ +-------------+   +-------------+
   | root <- swe       | | scout       |   | watcher     |
   |    \-> reviewer   | | (solo)      |   | (no peers)  |
   |    \-> tester     | +-------------+   +-------------+
   +-------------------+

         |  ^
         |  |  connect(scout, swe, channel="leads")
         v  |  -> graphs A and B merge into one;
            |     environments union, attached
            |     session stores merge.
            |
         |  |  disconnect(reviewer, tester, ...)
         v  |  -> if removing the link fragments
            |     the graph, A splits; each side
            |     gets a copy of the parent session.
```

Every observable thing — text chunks, tool activity, channel messages,
topology changes — surfaces through one event bus
(`EngineEvent` + `EventFilter`). Whether you have one graph or twelve,
you subscribe with one filter. The per-graph mental model above is a
*projection* of this engine.

What this buys you, beyond the single-team case:

- **Multiple sessions in one process** — a server hosts many user
  sessions side by side as independent graphs in one runtime engine.
- **Cross-graph rewiring at runtime** — combine two independent runs
  by drawing a channel between them; their session histories merge
  automatically.
- **Uniform observability** — one subscriber filter covers everything.
- **Layer-blindness preserved** — a creature still doesn't know it's
  in an engine. It only knows about its agent, its tools, and the
  channel handles its graph injected.

## Why it exists

Once creatures are portable — a creature runs by itself, the same
config works standalone — you need a way to compose them without
forcing them to know about each other. The terrarium is that way.

The invariant: a creature never knows it is in a terrarium. It
listens on channel names, it sends on channel names, that is all.
Remove it from the terrarium and it still runs as a standalone
creature.

## How we define it

Terrarium config:

```yaml
terrarium:
  name: my-team
  root:                         # optional; user-facing agent outside the team
    base_config: "@pkg/creatures/general"
    system_prompt_file: prompts/root.md   # team-specific delegation prompt
  creatures:
    - name: swe
      base_config: "@pkg/creatures/swe"
      output_wiring: [reviewer]           # deterministic edge → reviewer
      channels:
        listen:    [tasks, feedback]
        can_send:  [status]
    - name: reviewer
      base_config: "@pkg/creatures/swe"   # reviewer role via prompt, not a dedicated creature
      system_prompt_file: prompts/reviewer.md
      channels:
        listen:    [status]
        can_send:  [feedback, status]     # conditional: approve vs. revise stays on channels
  channels:
    tasks:    { type: queue }
    feedback: { type: queue }
    status:   { type: broadcast }
```

The runtime auto-creates one queue per creature (named after it, so
others can DM it) and, if a root exists, a `report_to_root` channel.

## How we implement it

- `terrarium/engine.py` — the `Terrarium` class. One per process.
  Owns the topology state, live creatures, environments, attached
  session stores, and the event-subscriber list. Async context manager
  (`async with Terrarium() as t:`) plus classmethod factories
  (`from_recipe`, `with_creature`, `resume`).
- `terrarium/topology.py` — pure-data graph model
  (`TopologyState`, `GraphTopology`, `ChannelKind`, `TopologyDelta`).
  No live agent references; testable without asyncio. The engine
  layers live state on top.
- `terrarium/creature_host.py` — `Creature`, the engine's per-creature
  wrapper. Combines the old standalone-agent and channel-aware
  surfaces into one type.
- `terrarium/recipe.py` — walks a `TerrariumConfig` and applies it to
  the engine: declare channels, auto-direct channels per creature,
  `report_to_root` when a root is declared, wire listen / send edges,
  inject channel triggers, start everything.
- `terrarium/channels.py` — channel injection (when a creature joins
  a graph with channels it listens to, a `ChannelTrigger` is added to
  its agent), plus the bodies of `connect_creatures` /
  `disconnect_creatures`.
- `terrarium/root.py` — the `assign_root` helper. Given a creature
  already in a graph, makes it the per-graph root: declares (or reuses)
  a `report_to_root` channel, wires every other creature in the graph
  to send on it, makes the root listen on every existing channel, and
  flips `creature.is_root = True`. Pure channel + wiring; tool
  registration and user-IO mounting stay at higher layers. Use it any
  time you build a graph imperatively and want the normal "one-team,
  one-root" topology without going through a recipe file.
- `terrarium/session_coord.py` — session merge / split policy. On a
  graph merge, both old stores are unioned into a new one. On a graph
  split, the parent store is duplicated to each side.
- `terrarium/events.py` — the `EngineEvent` taxonomy plus
  `EventFilter`, `ConnectionResult`, `DisconnectionResult`.

Top-level re-exports are stable: `from kohakuterrarium import
Terrarium, Creature, EngineEvent, EventFilter`. For user-facing
management concerns above the engine, use [`Studio`](../studio.md).

## What you can therefore do

- **Explicit specialist teams.** Two `swe` creatures cooperating
  through a `tasks` / `review` / `feedback` channel topology, with a
  prompt-driven reviewer role.
- **User-facing root agent.** See [root-agent](root-agent.md). Lets the
  user talk to one agent and have that agent orchestrate the team.
- **Deterministic pipeline edges via output wiring.** Declare in the
  creature's config that its turn-end output flows to the next stage
  automatically — no dependency on the LLM remembering `send_message`.
- **Hot-plug specialists.** Add a new creature mid-session without
  restart; the existing channels pick it up.
- **Non-destructive monitoring.** Attach a `ChannelObserver` to see
  every message in a queue channel without competing with the real
  consumers.

## Output wiring alongside channels

Channels are the original (and still correct) answer for **conditional
and optional traffic**: a critic that approves *or* revises, a status
broadcast anyone may read, a group-chat side-channel. They rely on the
creature calling `send_message`.

Output wiring is a separate, framework-level path: a creature declares
`output_wiring` in its config, and at turn-end the runtime emits a
`creature_output` TriggerEvent straight into the target's event queue.
No channel, no tool call — the event travels the same path any other
trigger uses.

Use wiring for the **deterministic pipeline edge** ("always next goes
to runner"). Keep channels for the conditional / broadcast / observation
cases wiring can't express. The two compose cleanly in a single
terrarium — the kt-biome `auto_research` and `deep_research` terrariums
do exactly that.

See [the terrariums guide](../../guides/terrariums.md#output-wiring)
for the config shape and mixed patterns.

## Position, honestly

We treat terrarium as a **proposed architecture** for horizontal
multi-agent rather than a fully settled one. The pieces work together
today (wiring + channels + hot-plug + observation + lifecycle pings to
root), and the kt-biome terrariums exercise them end to end. What we're
still learning is the idiom: when to prefer wiring vs. channels, how
to express conditional branches without hand-rolled channel plumbing,
how to surface wiring activity in the UI on par with channel traffic.

Use it where the workflow is genuinely multi-creature and you want the
creatures to stay portable. Use sub-agents (vertical) when the task
naturally decomposes inside one creature — vertical stays simpler for
most "I need context isolation" instincts. Both are legitimate; the
framework doesn't pick.

For the full set of improvements we're exploring (UI surfacing of
wiring events, conditional wiring, content modes, wiring hot-plug), see
[the ROADMAP](../../../ROADMAP.md).

## Don't be bounded

A terrarium without a root is legitimate (headless cooperative
work). A root without creatures is a standalone agent with special
tools. A creature can be a member of zero, one, or many terrariums
across different runs — terrariums do not taint creatures.

## See also

- [Multi-agent overview](README.md) — vertical vs horizontal.
- [Root agent](root-agent.md) — the user-facing creature outside the team.
- [Channel](../modules/channel.md) — the primitive terrariums are made of.
- [ROADMAP](../../../ROADMAP.md) — where terrariums are going.
