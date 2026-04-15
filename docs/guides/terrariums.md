# Terrariums

A **terrarium** is KohakuTerrarium's multi-agent composition layer.

It wires standalone creatures together through channels, manages lifecycle, and provides observation surfaces. It does not add its own LLM reasoning layer.

That distinction matters:

- a creature is the actual agent
- a terrarium is the runtime that connects creatures

Terrariums are useful, but they are not the main concept of the framework. The intelligence-bearing unit is still the creature.

For the deeper architectural model, see [Terrariums Concept](../concepts/terrariums.md).

## What a terrarium does

A terrarium is responsible for:

- loading creature configs
- creating shared channels
- wiring which creatures listen and send where
- injecting channel-based triggers
- starting and stopping all creature runtimes
- exposing observation and management surfaces

A terrarium is not where the intelligence lives. The intelligence stays inside each creature.

## Core terrarium pieces

A terrarium usually contains:

- one `terrarium.yaml`
- one or more creature references
- one or more channels
- optionally a root agent or interface-facing control path

A typical layout looks like this:

```text
my-terrarium/
  terrarium.yaml
  creatures/
    analyst/
    writer/
    reviewer/
```

In practice, creature configs can also live elsewhere or come from installed packages.

## Basic shape of a terrarium config

```yaml
terrarium:
  name: research_team

  creatures:
    - name: analyst
      config: ./creatures/analyst
      channels:
        listen: [tasks]
        can_send: [notes, team_chat]

    - name: writer
      config: ./creatures/writer
      channels:
        listen: [notes]
        can_send: [drafts, team_chat]

  channels:
    tasks:
      type: queue
      description: Work requests from the user or root agent
    notes:
      type: queue
      description: Analyst output for the writer
    drafts:
      type: queue
      description: Draft output
    team_chat:
      type: broadcast
      description: Shared status updates
```

## How to think about channels

Channels are the connective tissue of a terrarium.

### Queue channel

Use a queue when each message should be consumed by one receiver.

Good for:

- pipeline stages
- handoff workflows
- work queues

### Broadcast channel

Use a broadcast channel when multiple creatures should all observe the same message.

Good for:

- status updates
- shared context
- coordination chatter

See [Channels](../concepts/channels.md) for the full conceptual model.

## Common terrarium patterns

### Pipeline

One creature hands work to the next.

```text
tasks -> analyst -> notes -> writer -> drafts -> reviewer
```

### Team with shared chat

Creatures have specialized roles and share a broadcast channel.

```text
team_chat (broadcast)
  analyst
  reviewer
  planner
```

In practice, many useful terrariums combine both.

## Running a terrarium

Run a local terrarium:

```bash
kt terrarium run path/to/terrarium
```

Run an installed terrarium:

```bash
kt terrarium run @kt-defaults/terrariums/swe_team
```

`kt terrarium run` defaults to `tui`.

Useful options:

```bash
kt terrarium run @kt-defaults/terrariums/swe_team --mode tui
kt terrarium run @kt-defaults/terrariums/swe_team --mode cli
kt terrarium run path/to/terrarium --mode plain --observe
kt terrarium run path/to/terrarium --no-session
kt terrarium run path/to/terrarium --log-level DEBUG
```

### Mode behavior

#### `tui`

The default terrarium runtime surface.
Best when you want the full terrarium UI.

#### `cli`

If the terrarium defines a root agent, `cli` mode mounts that root agent.
If there is no root agent, `cli` mode auto-mounts the first creature and warns that the output of other creatures is not directly surfaced there.

#### `plain`

Minimal terminal surface.
Useful for automation, logs, and service-style operation.

In plain mode, you can explicitly enable or disable channel observation with `--observe` and `--no-observe`.

For exact command syntax, see [CLI Reference](../reference/cli.md).

## Root agent vs no root agent

A terrarium can be operated in different ways.

### With a root agent

A root agent sits outside the team and uses terrarium management tools to coordinate it.

Use this when you want:

- one main point of interaction
- orchestration through a controlling creature
- an experience closer to interacting with one top-level agent that manages a team underneath

### Without a root agent

The user or surrounding system injects work directly into channels or uses the API to control the runtime.

Use this when you want:

- simpler automation
- service-style orchestration
- explicit channel-level control

In some non-root flows, the runtime can seed an initial message into a channel for startup.

## Creature reuse inside a terrarium

A terrarium should reuse creatures rather than redefine them.

A good terrarium config says:

- which creature to run
- what that creature can hear
- what that creature can send

It should not restate the creature's internal behavior.

This is one of the strongest design properties in KohakuTerrarium.

## Practical design advice

### Put role logic in creatures

Examples:

- coding behavior belongs in an `swe`-style creature
- review behavior belongs in a reviewer creature
- research behavior belongs in a researcher creature

### Put collaboration logic in the terrarium

Examples:

- who sends tasks to whom
- whether a channel is queue or broadcast
- whether there is a root agent
- whether outputs are observed or logged

That split keeps the system understandable as it grows.

## Example terrarium design

```yaml
terrarium:
  name: code_review_team

  creatures:
    - name: developer
      config: "@kt-defaults/creatures/swe"
      channels:
        listen: [tasks, feedback]
        can_send: [review, team_chat]

    - name: reviewer
      config: "@kt-defaults/creatures/reviewer"
      channels:
        listen: [review]
        can_send: [feedback, team_chat]

  channels:
    tasks:
      type: queue
      description: Work assigned to the developer
    review:
      type: queue
      description: Work product that needs review
    feedback:
      type: queue
      description: Reviewer feedback to the developer
    team_chat:
      type: broadcast
      description: Shared status channel
```

This is a good example of hierarchy:

- creature configs define agent behavior
- the terrarium defines collaboration topology

## Terrarium sessions and observation

Terrariums can be persisted and resumed just like standalone creatures.

A terrarium session can include:

- root-agent events
- per-creature events
- conversation snapshots for each creature
- channel message history
- topology metadata such as creatures and channels

When resumed into the terrarium UI, these histories can be replayed into root tabs, creature tabs, and channel tabs.

See [Sessions](sessions.md).

## When to use a terrarium

Use a terrarium when you need:

- multiple creatures with distinct roles
- explicit communication through channels
- reusable team topologies
- observation across a multi-agent runtime

Do not start with a terrarium just because you assume multi-agent is the default.
In KohakuTerrarium, it usually is not.
Start with a creature unless your problem is truly about multi-creature collaboration.

## Related reading

- [Getting Started](getting-started.md)
- [Creatures](creatures.md)
- [Sessions](sessions.md)
- [Channels](../concepts/channels.md)
- [Terrariums Concept](../concepts/terrariums.md)
- [Examples](examples.md)
