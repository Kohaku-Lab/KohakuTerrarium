---
title: Root agent
summary: A creature outside the terrarium that represents the user — user-facing surface, management toolset, topology awareness.
tags:
  - concepts
  - multi-agent
  - root
---

# Root agent

## What it is

A **root agent** is a creature that sits *outside* a terrarium and
represents the user inside it. Structurally it is just another
creature: same config, same modules, same lifecycle. What makes it
"root" is:

1. It is positioned outside the team — the user talks to root; root
   talks to the terrarium.
2. It automatically receives the **terrarium-management toolset**
   (`terrarium_create`, `terrarium_send`, `creature_start`,
   `creature_stop`, `creature_status`, `terrarium_status`, …).
3. It auto-listens to every shared channel, and receives the
   dedicated `report_to_root` queue.

## Why it exists

A bare terrarium is headless — creatures cooperating through channels
with nobody driving. That works for some ambient workflows. For
interactive use, a human needs a single counterparty to talk to. The
root is that counterparty.

You could in principle do this with a normal creature plus manual
wiring, but getting the toolset and the listen-wiring right every
time is tedious. Making "root" a first-class position in the
terrarium config removes that boilerplate.

## How we define it

```yaml
terrarium:
  root:
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md     # team-specific delegation prompt
    controller:
      reasoning_effort: high
  creatures:
    - ...
  channels:
    - ...
```

Anything valid in an agent config is valid inside `root:`. Inheritance
(`base_config`) works the same way.

Authoring note: kt-biome does **not** ship a generic `root` creature.
Each terrarium owns its own `root:` block and a co-located
`prompts/root.md` that knows its specific team — "coding → send to
`driver`" reads better than "coding → send to the swe creature." The
framework handles everything else.

The runtime does three things with the root agent, regardless of what
you put in its config:

- Injects the management toolset (`terrarium_create`, `terrarium_send`,
  `creature_start`, `creature_stop`, `creature_status`,
  `terrarium_status`, …) into its registry.
- Auto-listens to every creature channel so it sees all team activity.
- Auto-generates a "terrarium awareness" prompt section listing the
  bound terrarium's creatures and channels, and appends it to root's
  system prompt.
- Makes root the one the user interacts with directly (TUI / CLI / web).

Your `prompts/root.md` only needs to carry delegation style /
personality — the topology awareness is framework-supplied.

## How we implement it

`terrarium/factory.py:build_root_agent` is called *after* creatures
are built. It creates the root with the shared environment (so the
management tools can see creatures and channels), registers the
`TerrariumToolManager` into its registry, and wires output back to
the user transport.

The root is built but not started until the user actually engages
with the terrarium — that lets `kt terrarium run` show team status
before the root wakes up.

## What you can therefore do

- **User-facing conductor.** The user asks root "have the SWE fix the
  auth bug, then have the reviewer approve it." Root sends messages
  through channels and monitors `report_to_root` for completion.
- **Dynamic team construction.** A root can `creature_start` new
  specialists based on the current task, then `creature_stop` them
  when done.
- **Terrarium bootstrapping.** A root agent can itself create and
  manage *other* terrariums via `terrarium_create`.
- **Observability pivot.** Because root auto-listens to everything,
  it is the natural place to run summarisation plugins, alerting
  rules, etc.

## Don't be bounded

Terrariums without roots are perfectly valid — think headless
pipelines, cron-driven coordination, batch jobs. A root is a
convenience for interactive use. And a root is still "just a
creature" — any pattern you can apply to a normal creature
(interactive sub-agents, plugins, custom tools) applies to root too.

## See also

- [Terrarium](terrarium.md) — the layer root lives on top of.
- [Multi-agent overview](README.md) — where root fits in the model.
- [reference/builtins.md — terrarium_* tools](../../reference/builtins.md) — the management toolset.
