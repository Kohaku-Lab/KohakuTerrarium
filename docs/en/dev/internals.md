---
title: Internals
summary: How the runtime fits together — event queue, controller loop, executor, subagent manager, plugin wrap.
tags:
  - dev
  - internals
---

# Framework internals

Implementation-level map of the runtime, grouped into creature runtime, Terrarium engine, and Studio/API management layers.
Reader is assumed to have `src/kohakuterrarium/` open alongside this
doc. Concept docs under `../concepts/` explain *why*; this explains
*where*. The public Python API reference (`plans/inventory-python-api.md`)
has signatures.

Sixteen flows are documented below. They are grouped as:

1. **Agent runtime** — lifecycle, controller loop, tool pipeline,
   sub-agents, triggers, prompt aggregation, plugins.
2. **Persistence & memory** — session persistence, compaction.
3. **Terrarium, Studio & adapters** — graph runtime, channels, environment
   vs session, Studio management facade, API/CLI adapters, compose algebra, package system, MCP.

A final [Cross-cutting invariants](#cross-cutting-invariants) section
collects the rules that apply system-wide.

---

## 1. Agent runtime

### 1.1 Agent lifecycle (standalone creature)

The CLI entry is `cli/run.py:run_agent_cli()`. It validates the config
path, picks an I/O mode (`cli` / `plain` / TUI), optionally builds a
`SessionStore`, then calls `Agent.from_path(config_path, …)` and
dispatches to `_run_agent_rich_cli()` or `agent.run()`.

`Agent.__init__` (`src/kohakuterrarium/core/agent.py:146`) runs
bootstrap in a fixed order: `_init_llm`, `_init_registry`,
`_init_executor`, `_init_subagents`, `_init_output`, `_init_controller`,
`_init_input`, `_init_user_commands`, `_init_triggers`. The mixin
layout is `AgentInitMixin` (`bootstrap/agent_init.py`) + `AgentHandlersMixin`
(`core/agent_handlers.py`) + `AgentToolsMixin` (`core/agent_tools.py`).

Registry init now has three distinct phases: wire creature-declared tools,
drop any provider-native tools unsupported by the active provider, then
auto-inject the provider-native tools advertised by that provider unless the
creature opted them out.

`await agent.start()` (`core/agent.py:186`) starts input and output
modules, wires TUI callbacks if any, starts the trigger manager, wires
completion callbacks, initializes MCP (connects servers and injects
tool descriptions into the prompt), initializes `CompactManager`,
loads plugins, publishes session info, and starts the termination
checker.

`await agent.run()` (`core/agent.py:684`) replays session events if
resuming, restores triggers, fires the startup trigger, then loops:
`event = await input.get_input()` → `_process_event(event)`. `stop()`
tears everything down in reverse order. The agent owns: `llm`,
`registry`, `executor`, `session`, `environment`, `subagent_manager`,
`output_router`, `controller`, `input`, `trigger_manager`,
`compact_manager`, `plugins`.

See [concepts/foundations/composing-an-agent.md](../concepts/foundations/composing-an-agent.md)
for the conceptual picture. API signatures: `plans/inventory-python-api.md` §Core Agent Lifecycle.

### 1.2 Controller loop and event model

Everything flows through `TriggerEvent` (`core/events.py`). Fields:
`type, content, context, timestamp, job_id?, prompt_override?, stackable`.
Types include `user_input`, `idle`, `timer`, `context_update`,
`tool_complete`, `subagent_output`, `channel_message`, `monitor`,
`error`, `startup`, `shutdown`.

The event queue is in `core/controller.py:push_event` /
`_collect_events` (lines 252-299). Stackable events collected in the
same tick are merged into one turn's user message; non-stackable events
break a batch; anything past the batch is saved in `_pending_events`
for the next turn.

Per-turn flow in `agent_handlers.py:_run_controller_loop`:

1. Collect events into a turn context.
2. Build messages, stream from LLM.
3. Parse tool / sub-agent / command events as they arrive in the stream.
4. Dispatch each via `asyncio.create_task` (tools start *during*
   streaming, not after).
5. After streaming ends, `asyncio.gather` on direct-mode completions.
6. Push the combined feedback event; decide whether to loop.

See [concepts/modules/controller.md](../concepts/modules/controller.md)
and the [stream-parser impl-note](../concepts/impl-notes/stream-parser.md).

### 1.3 Tool execution pipeline

The stream parser (`parsing/`) emits events when it detects a tool
block in the configured `tool_format` — bracket (default:
`[/bash]@@command=ls\n[bash/]`), XML (`<bash command="ls"></bash>`),
or native (the LLM provider's own function-calling envelope). Each
detected tool becomes an executor task via
`executor.submit_from_event()`.

The executor (`core/executor.py`) stores `{job_id: asyncio.Task}` and
builds a `ToolContext` for each invocation with `working_dir`,
`session`, `environment`, file guards, a file-read-state map, the job
store, and the agent name.

Three modes:

- **Direct** — awaited in the same turn. Results batch into the next
  controller feedback event.
- **Background** — `run_in_background=true` in the tool's result. The
  task keeps running; completion emits a future `tool_complete` event.
- **Stateful** — sub-agents and similar long-running handles. Results
  are stored in `jobs` and retrieved with the `wait` framework command.

Invariants (enforced in `agent_handlers.py` and `executor.py`):

- Tools start the moment their block parses — not queued until the LLM
  stops talking.
- Multiple tools in one turn run in parallel (`asyncio.gather`).
- Tools marked `is_concurrency_safe = False` are serialized behind one
  shared executor lock while safe tools remain parallel.
- Provider-native tools are advertised to the provider but never executed
  through the tool runner.
- LLM streaming is never blocked on tool execution.

See [concepts/modules/tool.md](../concepts/modules/tool.md) and
[impl-notes/stream-parser.md](../concepts/impl-notes/stream-parser.md).
API: `plans/inventory-python-api.md` §Tool Execution.

### 1.4 Sub-agent dispatch

Sub-agents are spawned by `modules/subagent/manager.py:spawn`. Depth is
bounded by `config.max_subagent_depth`. A new `SubAgent`
(`modules/subagent/base.py`) reuses the parent's registry, LLM, and
tool format but maintains its own conversation.

Completion pushes a `subagent_output` event back to the parent
controller. If the sub-agent is configured with `output_to: external`,
its output streams directly to a named output module instead of the
parent.

Interactive sub-agents (`modules/subagent/interactive.py` +
`interactive_mgr.py`) stay alive across turns, absorb context updates,
and can be fed new prompts via `_feed_interactive()`. They persist in
the session store like top-level conversations.

Shared iteration budgets are resolved in `SubAgentManager._resolve_child_budget`:
`budget_allocation` creates a fresh child budget, otherwise `budget_inherit`
reuses the parent's budget object when present.

See [concepts/modules/sub-agent.md](../concepts/modules/sub-agent.md).

### 1.5 Trigger system

`modules/trigger/base.py` defines `BaseTrigger`: an async generator
that yields `TriggerEvent`s. `to_resume_dict()` / `from_resume_dict()`
handle persistence.

Built-ins: `TimerTrigger`, `IdleTrigger`, `ChannelTrigger`,
`HTTPTrigger`, monitor triggers. The `TriggerManager`
(`core/trigger_manager.py`) keeps a dict of triggers and their
background tasks. On start, it launches one task per trigger that
iterates `fire()` and pushes events into the agent's queue. The
`CallableTriggerTool` (`modules/trigger/callable.py`) wraps each universal trigger class so agents can hot-plug
triggers at runtime.

On resume, trigger state is rebuilt from `events[agent]:*` rows in the
session store.

See [concepts/modules/trigger.md](../concepts/modules/trigger.md).

### 1.6 Prompt aggregation

`prompt/aggregator.py:aggregate_system_prompt` assembles the final
system prompt in this order:

1. Base prompt (agent personality from `system.md`), rendered with
   Jinja2 via `render_template_safe` so undefined variables degrade to
   empty strings.
2. Tool documentation. In `skill_mode: dynamic` this is just name +
   one-line description; in `static` it is the full doc.
3. Tool-guidance contributions collected from live tool instances.
4. Procedural-skill index (byte-budgeted, enabled skills only).
5. Channel topology hints, generated by
   `terrarium/config.py:build_channel_topology_prompt` at creature build
   time.
6. Framework hints per tool format (bracket / xml / native), resolved
   through package-level and creature-level override maps.
7. Named-output model (how to write to `discord`, `tts`, etc.).

Parts are joined with double newlines. `system.md` must never contain
the tool list, tool call syntax, or full tool docs — those are
auto-aggregated or loaded on demand via the `info` framework command.

See [impl-notes/prompt-aggregation.md](../concepts/impl-notes/prompt-aggregation.md).

### 1.7 Plugin systems

Two independent systems:

**Prompt plugins** (`prompt/plugins.py`) contribute content into the
system prompt at aggregate time. They are sorted by priority. Built-ins
include `ToolList`, `FrameworkHints`, `EnvInfo`, `ProjectInstructions`.

**Lifecycle plugins** (`bootstrap/plugins.py` + manager in
`modules/plugin/`) hook into agent events. The manager evaluates
`should_apply` / `applies_to` before every hook call, supports
plugin-contributed controller commands and plugin-contributed termination
checkers, and treats `PluginBlockError` from a `pre_*` hook as a veto.

The post-LLM path is now a rewrite chain rather than a plain callback:
plugins can rewrite the final assistant text, after which the runtime emits
an `assistant_message_edited` activity event for auditability.

Packages declare plugins in `kohaku.yaml`; plugins listed in
`config.plugins[]` load when the agent starts.

See [concepts/modules/plugin.md](../concepts/modules/plugin.md).

---

## 2. Persistence & memory

### 2.1 Session persistence

Sessions live in a single `.kohakutr` file backed by KohakuVault
(SQLite). Tables in `session/store.py`: `meta`, `state`, `events`
(append-only), `channels` (message history), `subagents` (snapshots
before destruction), `jobs`, `conversation` (latest snapshot per
agent), `fts` (full-text index). Binary artifacts live beside the DB in
`<session>.artifacts/` and are managed by `session/artifacts.py`.

Writes happen on:

- every tool call, text chunk, trigger fire, and token-usage emission
  (event log),
- end of each turn (conversation snapshot),
- scratchpad write,
- channel send.

Resume (`session/resume.py`): load `meta`, load the conversation
snapshot per agent, restore scratchpad/state, restore triggers, replay
events to the output module (for scrollback), reattach sub-agent
conversations. Non-resumable state (open files, LLM connections, TUI,
asyncio tasks) is rebuilt from config.

`session/memory.py` + `session/embedding.py` provide FTS5 and vector
search over the event log. Embedding providers: `model2vec`,
`sentence-transformer`, `api`. Vectors are stored alongside event blocks
for hybrid search.

See [impl-notes/session-persistence.md](../concepts/impl-notes/session-persistence.md).

### 2.2 Context compaction

`core/compact.py:CompactManager` runs after every turn.
`should_compact(prompt_tokens)` checks whether prompt tokens exceed
80% of `max_context` (configurable via `compact.threshold` and
`compact.max_tokens`). On trigger it emits a `compact_start` activity
event, spawns a background task that runs the summarizer LLM
(main LLM or the separate `compact_model` if configured), and
atomically splices the summary into the conversation *between* turns.
The live zone — last `keep_recent_turns` turns — is never summarized.

The atomic-splice design means the controller never sees messages
vanish mid-turn. See
[impl-notes/non-blocking-compaction.md](../concepts/impl-notes/non-blocking-compaction.md)
for the full reasoning.

---

## 3. Multi-agent & serving

### 3.1 Terrarium engine

`terrarium/engine.py:Terrarium` is the runtime engine — one per
process, hosting every creature. The engine owns:

- `_topology: TopologyState` — pure-data graph model
  (`terrarium/topology.py`) tracking which creatures share which
  graphs, which channels exist, who listens / sends.
- `_creatures: dict[str, Creature]` — live wrappers
  (`terrarium/creature_host.py`).
- `_environments: dict[str, Environment]` — one per graph; holds
  `shared_channels`.
- `_session_stores: dict[str, SessionStore]` — one per attached graph.
- `_subscribers: list[_Subscriber]` — `EngineEvent` pub-sub.

A standalone agent is a 1-creature graph; a recipe is a connected
graph with channels. `Terrarium.with_creature(config)` is the solo
shortcut; `Terrarium.from_recipe(recipe)` walks a `TerrariumConfig`
via `terrarium/recipe.py:apply_recipe` (declare channels, add direct
channels per creature, add `report_to_root` when a root is declared,
wire listen / send edges, start everything). Creatures never learn
they are in a terrarium except through their channels and (optionally)
the topology hint baked into their system prompt.

**Channel injection.** When a creature joins a graph that has channels
it listens to, `terrarium/channels.py:inject_channel_trigger` adds a
`ChannelTrigger` to its `trigger_manager`. This is the one sanctioned
downward injection in the layer model: a creature in a graph does
know it has peers (it has to), but only via the handles the engine
gave it. Solo creatures get no injection.

**Hot-plug.** Topology can change at runtime. `Terrarium.connect(a, b,
channel=...)` may merge two graphs (environments union, channels
pool, attached session stores merge into one via
`terrarium/session_coord.py:apply_merge`). `Terrarium.disconnect` may
split a graph (parent session is duplicated to each side via
`apply_split`). The pure-data topology mutators in
`terrarium/topology.py` return a `TopologyDelta` whose
`kind in {"nothing", "merge", "split"}` drives the live updates.

**Session merge / split.** The unit of session is the connected
component of the graph. Topology changes that don't affect graph
membership reuse the existing store. `terrarium/session_coord.py`
implements both branches and emits `SESSION_FORKED` / `TOPOLOGY_CHANGED`
events.

**Event bus.** `terrarium/events.py:EngineEvent` is the unified
observable surface. Kinds cover text chunks, channel messages,
topology changes, session forks, creature lifecycle, processing
start / end, and errors. `Terrarium.subscribe(filter)` returns an
async iterator over events matching `EventFilter`. Each subscriber
gets its own queue; cancelling the iterator de-registers.

The legacy `terrarium/runtime.py:TerrariumRuntime` and
`serving/manager.py:KohakuManager` are still on disk for compatibility and
legacy CLI/embedding paths. The v1.3 HTTP route path uses
`api/deps.py:get_engine()` and the Studio route/session modules; there is no
new `KohakuManager` route dependency.

See [concepts/multi-agent/terrarium.md](../concepts/multi-agent/terrarium.md)
and [concepts/multi-agent/root-agent.md](../concepts/multi-agent/root-agent.md).

### 3.2 Channels

`core/channel.py` defines two primitives:

- `SubAgentChannel` — queue-backed, one consumer per message, FIFO.
  Supports `send` / `receive` / `try_receive`.
- `AgentChannel` — broadcast. Each subscriber holds its own queue via
  `ChannelSubscription`. Late subscribers miss old messages.

Channels live in a `ChannelRegistry` under `environment.shared_channels`
(terrarium-wide) or `session.channels` (per-creature private). Auto-
created channels: per-creature queues and `report_to_root`.
`ChannelTrigger` binds a channel to an agent's event stream, turning
incoming messages into `channel_message` events.

See [concepts/modules/channel.md](../concepts/modules/channel.md).

### 3.3 Environment vs Session

- `Environment` (`core/environment.py`) holds terrarium-wide state:
  `shared_channels`, optional shared context dict, session bookkeeping.
- `Session` (`core/session.py`) holds per-creature state: private
  channel registry (or aliased to environment's), `scratchpad`, `tui`
  ref, `extra` dict.

One session per agent instance. In terrariums, environment is shared
across all creatures; sessions are private. Creatures never touch
each other's sessions — shared state goes strictly through
`environment.shared_channels`.

See [concepts/modules/session-and-environment.md](../concepts/modules/session-and-environment.md).

### 3.4 Studio and adapter layer

`studio/` is the management facade above the Terrarium engine. It owns
catalog, identity/settings, active sessions, saved-session persistence, attach
policies, and editor workflows. `api/` routes and CLI commands should delegate
those policies to Studio namespaces rather than duplicating them.

`api/deps.py:get_engine()` exposes the per-process `Terrarium` singleton for
route handlers that need runtime graph access. Session chat/control routes use
Studio session modules and engine-backed `Creature.chat()` semantics.

`serving/` remains for `web.py` launch helpers and compatibility wrappers such
as `AgentSession` / `KohakuManager`; new route handlers should not build on
those wrappers.

### 3.5 Compose algebra internals

`compose/core.py` defines `BaseRunnable.run(input)` and
`__call__(input)`. Operator overloads wrap the composition:

- `__rshift__` (`>>`) → `Sequence`; a dict-valued `>>` becomes a
  `Router`.
- `__and__` (`&`) → `Product` (run in parallel).
- `__or__` (`|`) → `Fallback`.
- `__mul__` (`*`) → `Retry`.

Plain callables are auto-wrapped in `Pure`. `agent()` constructs a
persistent `AgentRunnable` (shares conversation across calls);
`factory()` constructs an `AgentFactory` that creates a fresh agent
per call. `iterate(async_iter)` loops over an async source and awaits
the full pipeline for each element. `effects.Effects()` records
side-effects attached to a pipeline (`pipeline.effects.get_all()`).

See [concepts/python-native/composition-algebra.md](../concepts/python-native/composition-algebra.md).

### 3.6 Package / extension system

Install: `packages.py:install_package(source, editable=False)`. Three
modes — git clone, local copy, or `.link` pointer for editable.
Landing dir: `~/.kohakuterrarium/packages/<name>/`.

Resolution: `resolve_package_path("@<pkg>/<sub>")` follows `.link`
pointers or walks the directory. Used by config loaders (e.g.,
`base_config: "@pkg/creatures/…"`) and CLI commands.

A `kohaku.yaml` manifest declares the package's `creatures`,
`terrariums`, `tools`, `plugins`, `io`, `triggers`, `skills`,
`commands`, `user_commands`, `prompts` / `templates`,
`framework_hints`, `llm_presets`, and `python_dependencies`.

Terminology:

- **Extension** — a Python module contributed by a package
  (tool / plugin / LLM preset).
- **Plugin** — a lifecycle-hook implementation.
- **Package** — the installable unit that may contain any of the
  above plus configs.

### 3.7 MCP integration

`mcp/client.py:MCPClientManager.connect(cfg)` opens a stdio or
HTTP MCP session, calls `session.initialize()`, discovers tools via
`list_tools`, and caches results into `self._servers[name]`.
`disconnect(name)` cleans up.

On agent start, after MCP has connected, the agent calls
`_inject_mcp_tools_into_prompt()`, which builds an "Available MCP
Tools" markdown block listing each server, tool, and param set. Agents
invoke MCP tools through the builtin `mcp_call(server, tool, args)`
meta-tool, plus `mcp_list` / `mcp_connect` / `mcp_disconnect`.

Transports: `stdio` (subprocess with stdin/stdout) and `streamable_http plus legacy http/sse`.

---

## Cross-cutting invariants

These apply across the flows above. Violating any of them breaks
something.

- **Single `_processing_lock` per agent.** Only one LLM turn runs at a
  time. Enforced in `agent_handlers.py`.
- **Parallel tool dispatch.** All tools detected in one turn start
  together. Sequential dispatch is a bug.
- **Unsafe-tool serialization.** Tools marked not concurrency-safe must
  share one serial lock; otherwise mutating operations race.
- **Provider-native tool bypass.** Provider-native tools must never reach
  executor execution.
- **Non-blocking compaction.** The conversation swap is atomic and
  only happens between turns. The controller never sees messages
  vanish mid-LLM-call.
- **Event stackability.** A burst of identical stackable events
  coalesces into one user message; non-stackable events always break a
  batch.
- **Backpressure.** `controller.push_event` awaits when the queue is
  full. Runaway triggers get throttled instead of dropping events.
- **Terrarium session isolation.** Creatures never touch each other's
  sessions. Shared state goes through `environment.shared_channels`,
  period.

If you change any flow, re-check these. The inventory
(`plans/inventory-runtime.md`) is the source of truth and should be
updated alongside the code.

TODO: the inventory does not yet cover the `compose/` package in
full detail (beyond what's here), the `commands/` framework-command
runtime, or the output router state machine. Expand those when the
next inventory pass lands.
