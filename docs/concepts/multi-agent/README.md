---
title: Multi-agent
summary: Two axes — vertical (sub-agents) and horizontal (terrarium + channels + output wiring) — and when to pick which.
tags:
  - concepts
  - multi-agent
  - overview
---

# Multi-agent

There are two distinct axes of multi-agent in KohakuTerrarium, and
they solve different problems. Know which one you actually need
before you reach for a terrarium.

## Vertical (monolithic)

```
      Main creature
       /   |   \
    sub   sub   sub
    (plan)(impl)(review)
```

A single main creature dispatches sub-agents. Each sub-agent has its
own context and its own prompt. Result: one conversation the user
sees, many specialist conversations the parent hides.

This is what Claude Code, OpenClaw, Oh-My-Opencode, and most modern
coding agents do.

- **When to use:** you have a task that naturally decomposes into
  phases, and you want to isolate context.
- **What KT gives you:** sub-agents are native. Configure them in
  `subagents[]`, call them by name. See [sub-agent](../modules/sub-agent.md).

## Horizontal (modular)

```
   +-- creature_a -------+      +-- creature_b -------+
   |     (spec agent)    | <==> |     (another spec)  |
   +---------------------+      +---------------------+
              shared channels + runtime
```

Several independent specialist creatures running side-by-side, each
with its own design. They talk through channels.

This is what CrewAI, AutoGen, and MetaGPT target.

- **When to use:** the task fits an explicit multi-role workflow, and
  the roles are genuinely different agents (different prompts, tools,
  models), not just different sub-tasks of one agent.
- **What KT gives you:** [terrariums](terrarium.md). A terrarium is a
  pure wiring layer — no LLM, no decision-making. It runs creatures
  and owns the channels between them.

## Rule of thumb

**Start with vertical.** Most "I need multi-agent" instincts are
actually "I need context isolation" or "I need a specialist prompt,"
both of which are solved by sub-agents.

Reach for a terrarium when you genuinely want *different creatures*
cooperating, and when the workflow is stable enough to be expressed as
a topology.

## Position, honestly

We treat terrarium as a **proposed architecture** for horizontal
multi-agent. The pieces work together today: channels for optional /
conditional traffic, **output wiring** for deterministic pipeline
edges (a framework-level config that auto-delivers a creature's
turn-end text into a target's event queue — no `send_message` needed),
plus hot-plug, observation, and lifecycle pings to root. kt-biome's
`auto_research`, `deep_research`, `swe_team`, and `pair_programming`
terrariums exercise these end-to-end.

What we're still learning is the idiom — when to prefer wiring vs.
channels, how to express conditional branches without hand-rolled
channel plumbing, how to surface wiring activity in the UI on par
with channel traffic. Those open questions live in the
[ROADMAP](../../../ROADMAP.md).

Use terrarium where you genuinely want different creatures
cooperating. Use sub-agents when the task decomposes naturally inside
one creature — vertical stays simpler for most "I need context
isolation" instincts. The framework doesn't pick between them.

## What's in this section

- [Terrarium](terrarium.md) — the horizontal wiring layer.
- [Root agent](root-agent.md) — the creature that sits outside a
  terrarium and represents the user.

## See also

- [Sub-agent](../modules/sub-agent.md) — the vertical primitive.
- [Channel](../modules/channel.md) — the substrate both terrariums and
  some sub-agent patterns rely on.
