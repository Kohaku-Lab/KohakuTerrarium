---
title: Terrarium
summary: The horizontal wiring layer — channels for optional traffic, output wiring for deterministic edges, hot-plug and observation on top.
tags:
  - concepts
  - multi-agent
  - terrarium
---

# Terrarium

## What it is

A **terrarium** is a pure wiring layer that runs several creatures
together. It has no LLM of its own, no intelligence, and no decisions.
It does exactly two things:

1. It is a **runtime** that manages creature lifecycles.
2. It owns a set of **shared channels** the creatures can use to talk
   to each other.

That is the entire contract.

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

- `terrarium/runtime.py` — `TerrariumRuntime` orchestrates startup in
  a fixed order (create shared channels → create creatures → wire
  triggers → build root last, unstarted).
- `terrarium/factory.py` — `build_creature` loads a creature config
  (with `@pkg/...` resolution), creates the `Agent` with shared
  environment + private session, registers one `ChannelTrigger` per
  listen channel, and injects a channel-topology paragraph into the
  system prompt.
- `terrarium/hotplug.py` — `add_creature`, `remove_creature`,
  `add_channel`, `remove_channel` at runtime.
- `terrarium/observer.py` — `ChannelObserver` for non-destructive
  monitoring (so dashboards can watch without consuming).
- `terrarium/api.py` — `TerrariumAPI` is the programmatic facade; the
  terrarium-management builtin tools (`terrarium_create`,
  `creature_start`, `terrarium_send`, …) route through it.

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
