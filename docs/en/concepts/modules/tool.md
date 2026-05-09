---
title: Tool
summary: Named capabilities the LLM can invoke — shell commands, file edits, web searches, and more.
tags:
  - concepts
  - module
  - tool
---

# Tool

## What it is

A **tool** is how an agent *does* something. It is an executable
capability, registered with the controller, that the LLM can call by
name with arguments.

In most people's mental model a tool is "a function the LLM can call":
`bash`, `read`, `write`, `grep`, `web_search`. That is accurate but
incomplete. A tool can also be a message bus to another agent, a
state-machine handle, a nested creature, a permission gate, or all of
those at once.

## Why it exists

A chat bot is just a mouth. Tools give the agent hands. Without them
the LLM can only speak — with them it can do arbitrary work in the
world.

The framework's job is to make tool execution *cheap to use and cheap
to write*: streaming-aware dispatch, parallel execution, context
propagation, background jobs, and typed metadata. Every existing agent
product re-implements some subset of that; putting it in the substrate
once stops that cycle.

## How we define it

A tool implements:

- a **name** and a short description (auto-inserted into the system prompt)
- an **args schema** (`parameters`), JSON-Schema-compatible
- an async **`execute(args, context)` → `ToolResult`**
- an **execution mode**: `direct`, `background`, or `stateful`
- optional **full documentation** (`get_full_documentation()`) loaded
  on demand via the `info` framework command
- optional **prompt contribution** — a short hint paragraph inserted once
  into the aggregated system prompt
- optional **concurrency metadata** — whether this tool is safe to run in
  parallel with other mutating tools

There are now two broad tool families:

- **Runner-executed tools** — the normal case. The executor actually calls
  the tool's Python implementation.
- **Provider-native tools** — the provider performs the capability itself.
  The tool still appears in the registry and prompt, but the executor must
  not run it. Instead the LLM provider translates it into its own wire-format
  tool declaration and returns structured content back. `image_gen` on Codex
  is the shipped example.

Execution modes:

- **Direct** — await the tool within the same turn; feed the result
  back as a `tool_complete` event.
- **Background** — submit and release; result arrives in a later event.
- **Stateful** — multi-turn interaction; a generator-like tool that
  yields intermediate results the agent can react to.

Parallelism is split from execution mode. Multiple direct tools can still
start in the same turn. Tools marked concurrency-safe run fully in parallel;
tools marked unsafe are serialized behind one shared lock so destructive file
or shell operations do not race each other.

## How we implement it

Tools are registered in the `Registry` (`core/registry.py`). The
controller's stream parser detects tool blocks as they close and calls
`Executor.submit_from_event(...)` immediately. The executor creates an
`asyncio.Task`; multiple tools run in parallel.

In native tool-calling mode the provider also receives a structured schema
for each registered tool. Provider-native tools are filtered out of executor
execution and instead passed to the provider as native capabilities.

Each tool execution receives a `ToolContext` carrying:

- the creature's working directory,
- the session (scratchpad, private channels),
- the environment (shared channels, if any),
- file guards (read-before-write, path safety),
- file-read-state (for dedup),
- the agent name,
- the job store (so the `wait` / `read_job` framework commands can find the tool's job).

Built-in tools include shell (`bash`), Python (`python`), file ops
(`read`, `write`, `edit`, `multi_edit`), search (`glob`, `grep`,
`tree`), JSON (`json_read`, `json_write`), web (`web_fetch`,
`web_search`), communication (`send_message`), memory (`scratchpad`,
`search_memory`), introspection (`info`, `stop_task`), provider-native
media (`image_gen` on supported providers), and graph management
(`group_add_node`, `group_channel`, `group_wire`, …; registered on
privileged nodes only).

## What you can therefore do

- **Tools as message busses.** `send_message` writes to a channel; a
  `ChannelTrigger` on another creature reads it. Two tools + one
  trigger reproduce a group-chat pattern with zero new primitives.
- **Tools as state handles.** The `scratchpad` tool is a classic KV
  API; any cooperating tools can rendezvous through it.
- **Tools that install triggers.** Any universal trigger class
  (`TimerTrigger`, `ChannelTrigger`, `SchedulerTrigger` by default) can
  be exposed as a tool of its own — listing it under `tools:` with
  `type: trigger` makes `add_timer` / `watch_channel` / `add_schedule`
  show up in the tool list, and calling one installs that trigger on
  the live `TriggerManager`. `group_add_node` spawns a new creature
  into the caller's graph.
- **Tools with built-in guidance.** A tool can contribute a short prompt
  section telling the model how to use it safely or effectively. Those
  hints are aggregated deterministically between the tool list and the
  framework hints.
- **Tools that are provider capabilities.** A tool can be real to the
  model but virtual to the runtime: `image_gen` is registered like a tool,
  yet Codex performs image generation itself and the framework persists the
  returned artifact into the session.
- **Tools that wrap sub-agents.** Any sub-agent invocation is itself
  tool-shaped, because the LLM calls it by name with args.
- **Tools that run agents.** Because tools are plain Python, a tool can
  contain an agent — e.g. a guard tool that runs a small judging agent
  on the arguments before dispatching the real action. See
  [patterns](../patterns.md).

## Don't be bounded

Tools do not have to be "pure." They can mutate state, start
long-running work, coordinate with other creatures, or orchestrate
entire terrariums. They also do not have to be obvious: a tool whose
only effect is to mark a session as "ready for compaction" is
legitimate. The abstraction is "a thing the LLM can call"; the
framework does not police what happens behind the call.

## See also

- [impl-notes/stream-parser](../impl-notes/stream-parser.md) — why tools start before the LLM stops.
- [Sub-agent](sub-agent.md) — the "also a tool" sibling.
- [Channel](channel.md) — the other half of tool-as-message-bus.
- [Patterns](../patterns.md) — surprising uses of tools.
- [reference/builtins.md — Tools](../../reference/builtins.md) — the complete catalogue.
