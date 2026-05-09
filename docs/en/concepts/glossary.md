---
title: Glossary
summary: Plain-English definitions for the vocabulary used across the docs.
tags:
  - concepts
  - glossary
  - reference
---

# Glossary

Plain-English definitions for the vocabulary KohakuTerrarium uses. If
you land in the middle of a doc and a word stops you, this page is the
lookup. Each entry points to the full concept doc.

## Creature

A self-contained agent. The first-class abstraction in KohakuTerrarium.
A creature has a controller, tools, triggers, (usually) sub-agents,
input, output, a session, and optional plugins. It runs standalone or
inside a terrarium. Full: [what is an agent](foundations/what-is-an-agent.md).

## Controller

The reasoning loop inside a creature. Pulls events off a queue, asks
the LLM to respond, dispatches tool and sub-agent calls that come
back, feeds their results in as new events, decides whether to loop.
Not "the brain" — the LLM is the brain; the controller is the loop
that makes the LLM act over time. Full: [controller](modules/controller.md).

## Input

How the outside world hands a user's message to the creature. In
practice, just one specific kind of trigger — the one labelled
`user_input`. Built-ins include CLI, TUI, and `none` (trigger-only
creatures); audio/ASR is provided as opt-in custom modules. Full:
[input](modules/input.md).

## Trigger

Anything that wakes the controller without explicit user input.
Timers, idle detectors, webhooks, channel listeners, monitor
conditions. Each trigger pushes `TriggerEvent`s onto the creature's
event queue. Full: [trigger](modules/trigger.md).

## Output

How a creature talks back to its world. A router receives everything
the controller emits (text chunks, tool activity, token usage) and
fans it out to one or more sinks — stdout, TTS, Discord, file. Full:
[output](modules/output.md).

## Tool

A named capability the LLM can call with arguments. Shell commands,
file edits, web searches. A tool can also be a message bus, a state
handle, or a nested agent — the framework does not police what
happens behind the call. Full: [tool](modules/tool.md).

## Sub-agent

A nested creature spawned by a parent for a bounded task. Has its own
context and (usually) a subset of the parent's tools. Conceptually
also a tool — from the LLM's side, calling a sub-agent looks like
calling any tool. Full: [sub-agent](modules/sub-agent.md).

## TriggerEvent

The single envelope all external signals arrive in. User input, timer
fires, tool completions, channel messages, sub-agent outputs — all
become `TriggerEvent(type=..., content=..., ...)`. One envelope means
one code path. Full: [composing an agent](foundations/composing-an-agent.md).

## Channel

A named broadcast pipe. Every subscriber receives every message sent
on it — there is no queue/consume semantics at the [graph](#graph)
layer. Channels live either in a creature's private session or in a
graph's shared environment. A `send_message` tool plus a
`ChannelTrigger` is how cross-creature communication works. Full:
[channel](modules/channel.md).

## Output wiring

Configurable framework-level routing of a creature's turn-end output.
Declared via `output_wiring:` in the creature config; at the end of
every turn, the framework emits a `creature_output` `TriggerEvent`
into each listed target creature's event queue. No `send_message`
call required, no channel involved — it rides the same event path as
any other trigger. Use for deterministic pipeline edges; keep
channels for conditional / broadcast / observation traffic. Full:
[terrariums guide — output wiring](../guides/terrariums.md#output-wiring).

## creature_output (event type)

The `TriggerEvent` type the framework emits for each `output_wiring`
entry at turn-end. Context carries `source`, `target`, `with_content`,
`source_event_type`, and a per-source-creature `turn_index`. Plugins
on the receiving creature see it through the normal `on_event` hook.

## Session

Per-creature **private** state: the scratchpad, private channels, TUI
reference, a store of running jobs. Serialised to `.kohakutr` files.
One session per creature instance. Full:
[session and environment](modules/session-and-environment.md).

## Environment

**Shared** state across a terrarium: the shared channel registry plus
an optional shared context dict. Creatures get private-by-default,
shared-by-opt-in behaviour — they only see shared channels they
explicitly listen on. Full:
[session and environment](modules/session-and-environment.md).

## Scratchpad

A key-value store inside a creature's session. Lives across turns; can
be read and written by the `scratchpad` tool. Useful as working
memory, or as a rendezvous between cooperating tools.

## Plugin

Code that modifies the *connections between modules* instead of
forking a module. Two flavours: **prompt plugins** (contribute content
to the system prompt) and **lifecycle plugins** (hook `pre_llm_call`,
`post_tool_execute`, and so on). A `pre_*` hook can raise
`PluginBlockError` to abort an operation. Full: [plugin](modules/plugin.md).

## Skill mode

Config knob (`skill_mode: dynamic | static`) that decides whether the
system prompt ships full tool documentation up front (`static`,
bigger) or just names + one-liners that the agent expands on demand
via the `info` framework command (`dynamic`, smaller). Pure trade-off; nothing else
changes. Full: [prompt aggregation](impl-notes/prompt-aggregation.md).

## Framework commands

Inline directives the LLM can emit mid-turn to talk to the framework
without a full tool round-trip. They use the **same syntax family as
tool calls** — whatever `tool_format` the creature is configured with
(bracket, XML, or native). The word "command" here is about the
*intent* (talking to the framework rather than running a tool), not
about a different syntax.

In the default bracket format:

- `[/info]tool_or_subagent_name[info/]` — load full documentation for a tool or sub-agent on demand.
- `[/read_job]job_id[read_job/]` — read output from a running or completed background job (supports `--lines N` and `--offset M` flags in the body).
- `[/jobs][jobs/]` — list currently running background jobs (with their IDs).
- `[/wait]job_id[wait/]` — block the current turn until a background job finishes.

Command names share a namespace with tool names; the "read job
output" command is deliberately called `read_job` so it does not
collide with the `read` file-reader tool.

## Studio

The management layer above the [terrarium](#terrarium) engine. A
Python class (`kohakuterrarium.Studio`) that exposes six namespaces —
`catalog`, `identity`, `sessions`, `persistence`, `editors`, `attach`
— for the concerns every UI and automation otherwise re-implements:
package discovery, LLM profiles and API keys, active session
lifecycle, saved-session resume / fork / export, workspace
creature / module CRUD, and attach-policy advertisement. The web
dashboard, desktop app, `kt` CLI, and your own Python code all
delegate to Studio instead of duplicating logic. Studio is *not* a UI;
the dashboard is one of several adapters over it. Full:
[studio](studio.md).

## Terrarium

The runtime engine that hosts every running creature in the process.
A standalone agent is a 1-creature [graph](#graph) inside the engine;
a multi-creature team is a connected graph wired by channels. The
engine owns creature CRUD, channel CRUD, output wiring, [hot-plug](#hot-plug),
and the topology + session bookkeeping that follows graph changes
([auto-split / auto-merge](#auto-split--auto-merge)). It does *not*
run an LLM and does not have its own reasoning loop — that lives in
creatures. What it does decide is structural: which creatures share a
connected component, which session store backs which graph, where
each turn-end output gets delivered. Creatures don't know they are
in a terrarium; the same config still runs standalone. Full:
[terrarium](multi-agent/terrarium.md).

## Recipe

The YAML config file that populates a fresh [terrarium](#terrarium)
engine with a specific multi-creature setup. The engine itself is
always present; a recipe is a sequence of "add these creatures,
declare these channels, wire these edges, optionally promote one to
[root](#root)." Recipes are the source of truth on resume — when
a saved multi-creature session is reopened, the engine rebuilds the
topology from the recipe path stored in session metadata, not from a
frozen snapshot of the live graph.

## Graph

A connected component inside the [terrarium](#terrarium) engine: a
set of creatures that share at least one channel path. Two unrelated
creatures live in two graphs; drawing a channel between them merges
the graphs (and unions their session histories). Removing the last
channel between two halves splits a graph (and copies the history
into each side). The graph is the unit of session — creatures in the
same graph see the same `.kohakutr` file. Full:
[terrarium](multi-agent/terrarium.md).

## Root

The recipe keyword (`root:` in a `terrarium.yaml`) that designates
which node in the graph is the [privileged node](#privileged-node)
representing the user. The recipe loader marks it privileged, opens a
`report_to_root` channel that every other creature is wired to send
on, makes it listen on every other channel, and mounts it as the
user-facing surface (TUI / CLI / web). "Root" is a config convention,
not a separate runtime type — at runtime it is a privileged node with
the standard user-facing wiring. Full:
[privileged node](multi-agent/privileged-node.md).

## Privileged node

A creature that has been granted the [group tools](#group-tools) to
mutate the graph it belongs to: spawn or remove other creatures, draw
or delete channels, start or stop members. The node designated by
[`root:`](#root) is privileged by default; recipes can mark other
members privileged inline (`privileged: true`); engines accept
`is_privileged=True` at creature-add time. Tool-spawned worker
creatures (via `group_add_node`) are *not* privileged, so workers
cannot fork peers without explicit elevation. Privilege is a property
of the runtime creature handle, not the underlying agent config — the
same config can run privileged in one terrarium and unprivileged in
another. Full: [privileged node](multi-agent/privileged-node.md).

## Group tools

The set of built-in tools (`group_add_node`, `group_remove_node`,
`group_start_node`, `group_stop_node`, `group_channel`, `group_wire`,
`group_status`, `group_send`) that mutate or inspect a [graph](#graph)
from inside it. Registered only on
[privileged nodes](#privileged-node). Together they form the runtime
"graph editor" an LLM-driven privileged node uses to evolve a team
mid-run — every change emits an `EngineEvent` so observers and
runtime prompts stay in sync. Full:
[builtins reference](../reference/builtins.md).

## Hot-plug

Adding or removing a creature, channel, or wiring edge in a running
[terrarium](#terrarium) without restart. The engine handles the
bookkeeping: trigger injection and persistence attachment for new
pieces; trigger teardown and any [auto-split](#auto-split--auto-merge)
for removed pieces. Available imperatively
(`Terrarium.add_creature`, `connect`, `disconnect`) and via
[group tools](#group-tools) called by a privileged node.

## Auto-split / auto-merge

The engine's response to topology changes that affect connectivity.
When a `connect` crosses two graphs, the engine merges them — unions
their environments, copies both session stores into a single merged
store with `parent_session_ids` tracking lineage. When a `disconnect`
or a creature / channel removal severs the only path between two
halves, the engine splits the graph — allocates a fresh environment
per side, re-injects channel triggers against the new env, and
duplicates the session store into each side. All bookkeeping is
automatic; observers see new graph ids appear in `EngineEvent`
notifications.

## Package

An installable directory containing creatures, terrariums, custom
tools, plugins, LLM presets, and Python dependencies, described by a
`kohaku.yaml` manifest. Installed under `~/.kohakuterrarium/packages/`
via `kt install`. Referenced in configs and on the CLI with
`@<pkg>/<path>` syntax. Full: [packages guide](../guides/packages.md).

## kt-biome

The official out-of-the-box pack of useful creatures, terrariums, and
plugins, shipped as a package. Not part of the core framework — it's a
showcase + starting point. See
[github.com/Kohaku-Lab/kt-biome](https://github.com/Kohaku-Lab/kt-biome).

## Compose algebra

A small set of operators (`>>` sequence, `&` parallel, `|` fallback,
`*N` retry, `.iterate` async loop) for stitching agents into
pipelines in Python. Ergonomic sugar on top of the fact that agents
are first-class async Python values. Full:
[composition algebra](python-native/composition-algebra.md).

## MCP

Model Context Protocol — an external protocol for exposing tools to
LLMs. KohakuTerrarium connects to MCP servers over stdio, streamable HTTP, or legacy HTTP/SSE,
discovers their tools, and surfaces them to the LLM through meta-tools
(`mcp_call`, `mcp_list`, …). Full: [mcp guide](../guides/mcp.md).

## Compaction

The background process that summarises old conversation turns when the
context is getting full. Non-blocking: the controller keeps running
while the summariser works, and the swap happens atomically between
turns. Full: [non-blocking compaction](impl-notes/non-blocking-compaction.md).

## See also

- [Concepts index](README.md) — the full section map.
- [What is an agent](foundations/what-is-an-agent.md) — the deeper story that introduces most of these words together.
- [Boundaries](boundaries.md) — when to treat any of the above as optional.
