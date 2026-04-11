# Terrariums

A **terrarium** is KohakuTerrarium's multi-agent composition layer.

It wires standalone creatures together through channels, manages lifecycle, and provides observability. It does not add its own LLM reasoning layer.

That distinction matters:

- a creature is an agent
- a terrarium is the runtime that connects agents

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

## Two common terrarium patterns

## 1. Pipeline

One creature hands work to the next.

```text
tasks -> analyst -> notes -> writer -> drafts -> reviewer
```

Use this when work has a natural stage order.

## 2. Team with shared chat

Creatures have specialized roles and share a broadcast channel.

```text
team_chat (broadcast)
  analyst
  reviewer
  planner
```

Use this when you want coordination rather than strict stage-by-stage handoff.

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

Useful options:

```bash
kt terrarium run @kt-defaults/terrariums/swe_team --mode tui
kt terrarium run path/to/terrarium --no-session
kt terrarium run path/to/terrarium --log-level DEBUG
```

For exact command syntax, see [CLI Reference](../reference/cli.md).

## Root agent vs no root agent

A terrarium can be operated in different ways.

### With a root agent

A root agent sits outside the team and uses terrarium management tools to coordinate it.

Use this when you want:

- one main point of interaction
- orchestration through a controlling creature
- an experience closer to interacting with a single top-level agent that manages a team under the hood

### Without a root agent

The user or surrounding system injects work directly into channels or uses the API to control the runtime.

Use this when you want:

- simpler automation
- service-style orchestration
- explicit channel-level control

## Creature reuse inside a terrarium

A terrarium should reuse creatures rather than redefine them.

That is one of the strongest design properties in this codebase.

A good terrarium config says:

- which creature to run
- what that creature can hear
- what that creature can send

It should not restate the creature's internal behavior.

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

That split keeps your architecture understandable as the system grows.

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

## Terrarium persistence and observation

Terrariums can be persisted and resumed just like standalone creatures.

Typical operations:

```bash
kt terrarium run @kt-defaults/terrariums/swe_team
kt resume
kt resume --last
```

The runtime can also observe channel traffic and expose status through service APIs and UI surfaces.

See:

- [Sessions](sessions.md)
- [Serving Layer](../concepts/serving.md)
- [HTTP API](../reference/http.md)

## When to split into multiple terrariums

If one terrarium starts doing too many unrelated jobs, split it.

A good terrarium usually has one collaboration purpose, such as:

- software delivery workflow
- research workflow
- writing workflow
- monitoring workflow

If you find yourself inventing many disconnected channels for unrelated tasks, that is often a sign the system boundary is too large.

## Related reading

- [Getting Started](getting-started.md)
- [Creatures](creatures.md)
- [Configuration](configuration.md)
- [Channels](../concepts/channels.md)
- [Terrariums Concept](../concepts/terrariums.md)
- [Examples](examples.md)
