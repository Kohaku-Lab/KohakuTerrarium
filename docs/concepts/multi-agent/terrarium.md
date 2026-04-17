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
    base_config: "@pkg/creatures/root"
  creatures:
    - name: swe
      base_config: "@pkg/creatures/swe"
      channels:
        listen:    [tasks]
        can_send:  [review, status]
    - name: reviewer
      base_config: "@pkg/creatures/reviewer"
      channels:
        listen:    [review]
        can_send:  [status]
  channels:
    tasks:    { type: queue }
    review:   { type: queue }
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

- **Explicit specialist teams.** A `swe` creature and a `reviewer`
  creature cooperating through a `tasks` / `review` / `status` channel
  topology.
- **User-facing root agent.** See [root-agent](root-agent.md). Lets the
  user talk to one agent and have that agent orchestrate the team.
- **Hot-plug specialists.** Add a new creature mid-session without
  restart; the existing channels pick it up.
- **Non-destructive monitoring.** Attach a `ChannelObserver` to see
  every message in a queue channel without competing with the real
  consumers.

## The honest bit

Terrarium is experimental. Its current limitation: a terrarium's
progress depends on each creature routing its own output to the right
channel. If a model ignores the instruction — which happens — the
team can stall.

The [ROADMAP](../../../ROADMAP.md) plans for:

- **Configurable automatic round-output routing** — let users assign
  a channel that receives a creature's latest message for a round
  automatically.
- **Root lifecycle observation** — let the root see completion signals
  and inspect channel activity.
- **Configuration-first automation** — any new automation remains
  opt-in.
- **Dynamic terrarium management** — let a root add/remove creatures
  during runtime.

Until those land, prefer vertical (sub-agent) multi-agent when you
can. Terrariums are worth using now when the workflow is explicit and
the creatures can be trusted to follow their prompts.

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
