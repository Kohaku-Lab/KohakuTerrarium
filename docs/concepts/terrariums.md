# Terrariums

A **terrarium** is KohakuTerrarium's optional multi-agent wiring layer.

The key word is optional.
KohakuTerrarium starts from the creature, not from the terrarium.
A terrarium becomes useful when you want multiple creatures to collaborate through explicit channels.

## The basic idea

The **creature** handles the internal agent logic.
The **terrarium** handles the external composition between creatures.

```text
    Vertical (inside a creature)       Horizontal (between creatures)

         Controller                  planner <---> researcher
         /       \                        |              |
    sub-agent  sub-agent              channels        channels
                                          |              |
    Internal delegation              writer <-----> reviewer
    inside one creature
```

The boundary is clean: a creature does not need to change just because it is placed inside a terrarium.

## What a terrarium does

A terrarium is not an agent.
It has no LLM, no reasoning loop, and no internal intelligence of its own.

Its job is to:

1. load standalone creature configs
2. create shared channels connecting them
3. inject triggers so creatures react to channel messages
4. manage lifecycle and observation
5. provide multi-creature runtime surfaces

## Why this split matters

A lot of systems blur internal agent logic and external multi-agent wiring into one abstraction.
KohakuTerrarium keeps them separate.

That gives you a cleaner system:

- creature behavior stays inside the creature
- collaboration topology stays in the terrarium
- creatures remain reusable outside the terrarium
- the multi-agent layer stays simple instead of becoming another hidden controller

## The opacity principle

Creatures are treated as reusable units.
The terrarium wires them together, but it does not redefine their internal behavior.

```text
+-------------+     +-------------------+     +-----------------+
|  Creatures  |     |  Terrarium Layer  |     | Human Interface |
|             |<--->|                   |<--->|                 |
| Has:        |     | Has:              |     | Has:            |
| - LLM       |     | - Channels        |     | - CLI / TUI     |
| - Tools     |     | - Lifecycle       |     | - Web UI        |
| - Subagents |     | - Observation     |     | - Desktop app   |
| - Memory    |     | - Wiring          |     |                 |
+-------------+     +-------------------+     +-----------------+
```

## Communication model

Creatures communicate explicitly through channels.
The terrarium does not silently forward arbitrary output into shared collaboration state.

**Receiving** happens through terrarium-injected channel triggers.
**Sending** happens when a creature explicitly calls the message-sending tools.

That makes collaboration visible and intentional.

See [Channels](channels.md) for channel semantics.

## Topology patterns

Different topologies come from channel wiring, not from changing the creature abstraction.

```text
Pipeline:                    Hub-and-spoke:
  A --> [ch] --> B --> [ch]      coordinator --> [tasks] --> worker_1
       --> C                     coordinator --> [tasks] --> worker_2
                                 worker_* --> [results] --> coordinator

Group chat:                  Hybrid:
  A --+                         A --> [queue] --> B
  B --+--> [broadcast] --> all  all <--> [broadcast] <--> all
  C --+
```

## The root agent

A terrarium can optionally define a root agent.

The root agent sits outside the team and uses terrarium management tools to operate it.

This is useful when you want:

- one main point of interaction
- a controlling creature that delegates into the team
- a top-level interface over a multi-creature runtime

But the root agent is still not the terrarium itself.
The terrarium remains the wiring and runtime layer around creatures.

## Mode behavior in practice

Current runtime behavior matters for understanding how terrariums feel in use:

- `kt terrarium run` defaults to `tui`
- `cli` mode mounts the root agent when one exists
- if no root exists, `cli` mode auto-mounts the first creature and warns about partial output visibility
- `plain` mode can explicitly observe channels with `--observe` / `--no-observe`

These are runtime surfaces over the terrarium model, not changes to the core abstraction.

## Defining a terrarium

```yaml
terrarium:
  name: novel_writer
  creatures:
    - name: brainstorm
      config: ./creatures/brainstorm/
      channels:
        listen: [seed, team_chat]
        can_send: [ideas, team_chat]
    - name: planner
      config: ./creatures/planner/
      channels:
        listen: [ideas, team_chat]
        can_send: [outline, team_chat]
  channels:
    seed:      { type: queue, description: "User prompt" }
    ideas:     { type: queue, description: "Story concepts" }
    outline:   { type: queue, description: "Chapter outlines" }
    team_chat: { type: broadcast, description: "Shared context" }
```

The creature configs stay standalone. The terrarium adds the wiring.

## Terrarium sessions

Terrarium sessions store more than just top-level conversation.
They can preserve:

- root-agent events
- per-creature events
- conversation snapshots for each creature
- channel message history
- terrarium metadata such as declared creatures and channels

This is why terrarium sessions can later be replayed in the UI with root tabs, creature tabs, and channel tabs.

See [Sessions](../guides/sessions.md).

## When to use a terrarium

Use a terrarium when your problem is really about collaboration between multiple creatures.

Good reasons:

- multiple specialist creatures with different roles
- explicit message-passing through channels
- reusable team topologies
- multi-creature observation and coordination

Do not use a terrarium just because you assume multi-agent should be the default framing.
In KohakuTerrarium, the creature is still the first-class abstraction.

## Compact summary

- a **creature** defines one agent
- a **terrarium** defines how multiple creatures are wired together
- a terrarium is **pure wiring and runtime management**, not a second brain
- the creature remains the main reusable unit
- terrariums are useful when collaboration topology is the real problem

## Where to go next

- [Agents](agents.md)
- [Channels](channels.md)
- [Environment and Session](environment.md)
- [Terrariums Guide](../guides/terrariums.md)
