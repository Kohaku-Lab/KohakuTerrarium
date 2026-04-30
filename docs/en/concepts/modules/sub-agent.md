---
title: Sub-agent
summary: Nested creatures spawned by a parent for bounded tasks, with their own context and a subset of tools.
tags:
  - concepts
  - module
  - sub-agent
---

# Sub-agent

## What it is

A **sub-agent** is a nested creature spawned by a parent for a bounded
task. It has its own LLM conversation, its own tools (usually a subset
of the parent's), and its own (small) context. When it finishes, it
returns a condensed result and disappears.

The slides summary: *actually also a tool*. From the parent
controller's point of view, calling a sub-agent looks exactly like
calling any other tool.

## Why it exists

Context windows are finite. A real task — "explore this repo and tell
me how auth works" — can involve hundreds of file reads. Doing that
exploration in the parent's conversation blows the budget for the main
work. Doing it in a sub-agent usually spends a separate budget and returns
just the summary.

But that budget is now configurable. Sub-agents can have their own
multi-axis runtime budget (turns, tool calls, and optionally walltime),
let them run unbounded, or make them share the same legacy iteration
budget as the parent. Builtin sub-agents ship with a conservative minimum
runtime budget: soft/hard turns `40/60`, soft/hard tool calls `75/100`,
and no walltime limit.

A second reason: **specialisation**. A `critic` sub-agent prompted
specifically for review decisions will outperform a general agent
doing review as a side task. Sub-agents let you wire a specialist into
a generalist workflow without rewriting the generalist.

## How we define it

A sub-agent is a creature config + a parent registry. When spawned:

- it inherits the parent's LLM and tool format,
- it is given a subset of tools (the `tools` list in its sub-agent config),
- it runs a full Agent lifecycle (start → event-loop → stop),
- it may have its own unified `budget` plugin options (`turn_budget`, `tool_call_budget`, optional `walltime_budget`),
- it can additionally inherit the parent's legacy iteration budget, get its own
  `budget_allocation`, or run without a shared budget at all,
- its result is delivered as a `subagent_output` event on the parent,
  or streamed directly to the user if `output_to: external`.

Three flavours matter:

- **One-shot** (default) — spawned, runs to completion, returns once.
- **Output sub-agent** (`output_to: external`) — its text streams to
  the parent's `OutputRouter` in parallel with (or instead of) the
  controller's text. Think: the controller silently orchestrates; the
  sub-agent is what the user reads.
- **Interactive** (`interactive: true`) — persists across turns,
  receives context updates, can be fed new prompts. Useful for
  specialists that benefit from conversation continuity (a running
  reviewer, a persistent planner).

## How we implement it

`SubAgentManager` (`modules/subagent/manager.py`) spawns `SubAgent`s
(`modules/subagent/base.py`) as `asyncio.Task`s, tracks them by
job id, and delivers completions as `TriggerEvent`s.

Depth is bounded by `max_subagent_depth` (config-level) to prevent
runaway recursion. Cancellation is cooperative — the parent can invoke
`stop_task` to interrupt a running sub-agent. Runtime budgets are enforced by
the unified `budget` plugin, configured through `plugins[].options` with axes
such as `turn_budget`, `tool_call_budget`, and optional `walltime_budget`.
Auto-compaction is enabled separately with the `auto-compact` pack (which expands
to `compact.auto`). Shared legacy iteration budgets are resolved at spawn time:
`budget_allocation` wins, otherwise `budget_inherit: true` reuses the parent's
budget object if one exists.

Built-in sub-agents (in `kt-biome` + framework): `worker`, `plan`,
`explore`, `critic`, `response`, `research`, `summarize`,
`memory_read`, `memory_write`, `coordinator`.

## What you can therefore do

- **Plan / implement / review.** A parent with three sub-agents. The
  parent orchestrates; each sub-agent stays focused on one phase.
- **Silent controller.** Parent uses `output_to: external` on a
  `response` sub-agent. The controller does not emit text; only the
  sub-agent's reply reaches the user. This is how most kt-biome
  chat-style creatures work.
- **Persistent specialist.** An `interactive: true` reviewer that sees
  every turn and speaks only when it has something to say.
- **Nested terrariums.** A sub-agent can start a terrarium with
  `terrarium_create`. The substrate does not care.
- **Vertical-inside-horizontal.** A terrarium creature that itself
  uses sub-agents — mixing axes of multi-agent.

## Don't be bounded

Sub-agents are optional. A creature with tools alone is fine for
most short tasks. And because "sub-agent" is conceptually "a tool
whose implementation happens to be an entire agent," the distinction
can blur: a tool could spawn an agent in Python, and from the LLM's
point of view that is indistinguishable from a sub-agent call.

## See also

- [Tool](tool.md) — the "also a tool" framing.
- [Multi-agent overview](../multi-agent/README.md) — vertical (sub-agents) vs horizontal (terrariums).
- [Patterns — silent controller](../patterns.md) — the output-sub-agent idiom.
- [Sub-agent guide](../../guides/sub-agents.md) — configuring builtin/inline sub-agents, budgets, and runtime plugins.
- [reference/builtins.md — Sub-agents](../../reference/builtins.md) — the kit bag.
