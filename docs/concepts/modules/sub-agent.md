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
work. Doing it in a sub-agent spends a separate budget and returns
just the summary.

A second reason: **specialisation**. A `critic` sub-agent prompted
specifically for review decisions will outperform a general agent
doing review as a side task. Sub-agents let you wire a specialist into
a generalist workflow without rewriting the generalist.

## How we define it

A sub-agent is a creature config + a parent registry. When spawned:

- it inherits the parent's LLM and tool format,
- it is given a subset of tools (the `tools` list in its sub-agent config),
- it runs a full Agent lifecycle (start → event-loop → stop),
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
`stop_task` to interrupt a running sub-agent.

Built-in sub-agents (in `kt-defaults` + framework): `worker`, `plan`,
`explore`, `critic`, `response`, `research`, `summarize`,
`memory_read`, `memory_write`, `coordinator`.

## What you can therefore do

- **Plan / implement / review.** A parent with three sub-agents. The
  parent orchestrates; each sub-agent stays focused on one phase.
- **Silent controller.** Parent uses `output_to: external` on a
  `response` sub-agent. The controller does not emit text; only the
  sub-agent's reply reaches the user. This is how most kt-defaults
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
- [reference/builtins.md — Sub-agents](../../reference/builtins.md) — the kit bag.
