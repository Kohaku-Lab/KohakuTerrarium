# Framework internals

Implementation-level map of the runtime, grouped into three layers.
Reader is assumed to have `src/kohakuterrarium/` open alongside this
doc. Concept docs under `../concepts/` explain *why*; this explains
*where*. The public Python API reference (`plans/inventory-python-api.md`)
has signatures.

Sixteen flows are documented below. They are grouped as:

1. **Agent runtime** — lifecycle, controller loop, tool pipeline,
   sub-agents, triggers, prompt aggregation, plugins.
2. **Persistence & memory** — session persistence, compaction.
3. **Multi-agent & serving** — terrarium runtime, channels, environment
   vs session, serving layer, compose algebra, package system, MCP.

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
`create_trigger` builtin tool wraps this so agents can hot-plug
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
3. Channel topology hints, generated by
   `terrarium/config.py:build_channel_topology_prompt` at creature build
   time.
4. Framework hints per tool format (bracket / xml / native).
5. Named-output model (how to write to `discord`, `tts`, etc.).

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
`modules/plugin/`) hook into agent events.
`PluginManager.notify(hook, **kwargs)` awaits every enabled plugin's
matching method. A `PluginBlockError` raised from a `pre_*` hook
aborts the operation. Hooks are listed in the builtin inventory.

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
agent), `fts` (full-text index).

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

### 3.1 Terrarium runtime

`terrarium/runtime.py:TerrariumRuntime.start` (lines 85-180) pre-creates
shared channels, ensures a direct-queue per creature, adds
`report_to_root` if a root is present, builds each creature via
`terrarium/factory.py:build_creature`, starts creatures, builds the
root last (unstarted), then starts the termination checker.

`build_creature` loads the base config via `@pkg/...` or path, creates
`Agent(session=Session(creature_name), environment=shared_env, …)`,
registers one `ChannelTrigger` per listen-channel, and concatenates
the channel topology prompt onto the system prompt. Creatures never
learn they are in a terrarium except through their channels and
(optionally) topology hints.

The root agent gets a `TerrariumToolManager` attached to its
environment so it can use `terrarium_*` and `creature_*` tools. Root
is always outside, never a peer.

`terrarium/hotplug.py:HotPlugMixin` provides `add_creature`,
`remove_creature`, `add_channel`, `remove_channel` at runtime.
`terrarium/observer.py:ChannelObserver` attaches non-destructive
callbacks on channel sends so dashboards can watch queue channels
without consuming messages.

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

### 3.4 Serving layer

`serving/manager.py:KohakuManager` creates `AgentSession` or
`TerrariumSession` wrappers for transport code.
`AgentSession.send_input` pushes user-input events into the agent and
yields output-router events as JSON dicts: `text`, `tool_start`,
`tool_complete`, `activity`, `token_usage`, `compact_*`, `job_update`,
and so on.

Both the HTTP/WS API in `api/` and any Python embedding code use this
layer instead of touching `Agent` internals directly.

API signatures: `plans/inventory-python-api.md` §Serving.

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
`terrariums`, `tools`, `plugins`, `llm_presets`, and
`python_dependencies`.

Terminology:

- **Extension** — a Python module contributed by a package
  (tool / plugin / LLM preset).
- **Plugin** — a lifecycle-hook implementation.
- **Package** — the installable unit that may contain any of the
  above plus configs.

### 3.7 MCP integration

`mcp/client.py:MCPClientManager.connect(cfg)` opens a stdio or
HTTP/SSE session, calls `session.initialize()`, discovers tools via
`list_tools`, and caches results into `self._servers[name]`.
`disconnect(name)` cleans up.

On agent start, after MCP has connected, the agent calls
`_inject_mcp_tools_into_prompt()`, which builds an "Available MCP
Tools" markdown block listing each server, tool, and param set. Agents
invoke MCP tools through the builtin `mcp_call(server, tool, args)`
meta-tool, plus `mcp_list` / `mcp_connect` / `mcp_disconnect`.

Transports: `stdio` (subprocess with stdin/stdout) and `http/SSE`.

---

## Cross-cutting invariants

These apply across the flows above. Violating any of them breaks
something.

- **Single `_processing_lock` per agent.** Only one LLM turn runs at a
  time. Enforced in `agent_handlers.py`.
- **Parallel tool dispatch.** All tools detected in one turn start
  together. Sequential dispatch is a bug.
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
