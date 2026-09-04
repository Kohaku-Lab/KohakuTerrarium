# KohakuTerrarium

A universal agent framework for building any type of fully self-driven agent system.

## Project Overview

KohakuTerrarium is a Python framework that enables building any kind of agent system - from SWE agents like Claude Code to conversational bots like Neuro-sama to autonomous monitoring systems. The name "Terrarium" reflects how the framework allows you to build different self-contained agent ecosystems.

## Code Conventions

### File Organization
- Source code: `src/kohakuterrarium/`
- Frontend (Vue 3): `src/kohakuterrarium-frontend/`
- Creature templates: `creatures/`
- Terrarium templates: `terrariums/`
- Examples: `examples/` (agent-apps, terrariums, plugins, code)
- Documentation: `docs/` (en, zh-CN, zh-TW)
- Max lines per file: 600 (hard max: 1000, enforced by `tests/unit/test_file_sizes.py`)
- Highly modularized - one responsibility per module

### Coding style rules

Comment + code + docstring are all for **WHAT**. *How* and *why* live in docs
(`plans/`, `docs/`) — not in the source.

1. NO memo/noting/editorial comments (existing code may still violate this; new code must not)
2. A comment says what the code itself cannot say clearly
3. A docstring says what the class or function is *for*, so a reader can place it
4. Soft limits: inline comment blocks ≤ 2 lines, docstrings ≤ 14 lines.
   Not hard rules — exceeding one needs a reason, and "this is rationale"
   is not one (move it to a doc).

A local `PostToolUse` hook flags violations after each Python edit:
`.claude/hooks/comment_density.py`, wired in `.claude/settings.json`. Both live
under the gitignored `.claude/`, so the hook is **per-developer, not team-wide** —
a fresh clone will not have it. To adopt it, copy those two files, or run
`python .claude/hooks/comment_density.py` over a file with
`{"tool_input":{"file_path":"<abs path>"}}` on stdin. It only ever advises;
it exits 0 and blocks nothing.

### Import Rules
1. No imports inside functions (except optional dep and lazy import to avoid long init time)
2. Import grouping order:
   - Built-in modules
   - Third-party packages
   - KohakuTerrarium modules
3. Import ordering within groups:
   - `import` statements before `from` imports
   - Shorter paths before longer paths (by dot count)
   - Alphabetical order (a-z)

### Python Style
- Target: Python 3.10+ (CI matrix runs 3.10 through 3.14)
- Use modern type hints: `list`, `tuple`, `dict`, `X | None` (NOT `List`, `Tuple`, `Dict`, `Optional`, `Union`)
- Prefer `match-case` over deeply nested `if-elif-else`
- Full asyncio throughout (mark sync modules as "require blocking" or "can be to_thread")
- Practical dependencies allowed (pydantic, httpx, rich, etc.)

### Frontend Style
- Vue 3 + Vite, JavaScript only (no TypeScript)
- Run `npm run format:check` and `npm run build` before committing

### Development Setup
- Use `uv pip install -e ".[dev]"` for editable install
- **Never use `sys.path.insert` hacks** in examples or tests - always rely on proper package install
- Examples and tests should import from `kohakuterrarium.*` directly

### Logging (No print!)
- **Avoid naive `print()` in library code** - use structured logging
- Use custom logger based on `logging` module (NOT loguru)
- Format: `[HH:MM:SS] [module.name] [LEVEL] message`
- Color coding: DEBUG=gray, INFO=green, WARNING=yellow, ERROR=red
- **Avoid reserved LogRecord attributes** in extra kwargs: `name`, `msg`, `args`, `levelname`, `levelno`, `pathname`, `filename`, `module`, `lineno`, `funcName`, `created`, `msecs`, `relativeCreated`, `thread`, `threadName`, `process`, `processName`, `message`
- Exception: Test suites (`tests/`) can use simpler output

### Post-impl tasks
1. Verify all your impl follow the rules (ESPECIALLY in-function import!)
2. `black src/ tests/` and `ruff check src/ tests/`
3. Ensure new stuff has corresponding tests in the right tier (see "Test
   suite" below): new unit tests pin behaviour; touched folders/journeys
   get their integration/e2e workflow extended, not a new test function
4. Logically separated git commits and push (user may explicitly say "draft"; if so, don't push)

### Audit loop (multi-step impl work: REQUIRED)

For any task larger than a one-file change, do NOT stop at "tests
pass." Run this loop until it converges:

1. **Implement** the slice.
2. **Write new tests** that pin the behaviour you added. Negative
   cases (the bug you'd accidentally introduce) count more than
   positive cases.
3. **Execute the full test suite** for the affected tiers
   (unit/integration/e2e + frontend vitest). Lint too (`black`,
   `ruff`, `prettier`).
4. **Audit** the diff with a critical eye, in three categories:
   - **Clear bugs:** typos, wrong field names, off-by-ones,
     `await` missing on async calls, dead branches.
   - **Integrity bugs:** invariants you broke: state that's
     supposed to be in sync now drifts, two writers race a single
     dict, a cache outlives the thing it caches.
   - **Behavior bugs:** the code does what's typed but the wrong
     thing for the spec: wrong default, silently-swallowed
     error, condition gates the wrong branch.
5. **If you find any bug the tests didn't catch:** first augment
   the test so it *would* have caught it, confirm the augmented
   test fails on the unfixed code, then fix the bug. Tests that
   miss real bugs are evidence the test suite is the bug; patching
   tests first prevents the same blind spot next time.
6. **Loop** to step 3. Stop only when the audit finds nothing AND
   every test is green.

This loop is the difference between "I wrote code and tests passed"
and "I delivered working code." Treat the loop as part of the
definition-of-done, not optional polish.

### Test suite (three tiers)

`tests/` has three tiers, each a *different shape of test*, not just a
different size. `tests/README.md` is the full spec; the summary:

- **`tests/unit/`: one source file → one test (or test-class).** Tests
  an individual class / method against its real dependencies
  (deterministic stubs only for genuine I/O). Shape checks (`isinstance`,
  `key in dict`, `is not None`) are legitimate **here and only here**.
  Target: 95–100% line coverage per core-lib file; any sub-95% file
  needs a written justification in the test or a tracking issue.
- **`tests/integration/`: one core-lib folder → one test-class.** Each
  test method runs a **complete feature workflow end-to-end in a single
  function** (init → drive → read back → resume → verify), mirroring how
  the real consumer drives that folder. Splitting a workflow into
  separate "init" / "read" / "resume" tests is unit-tier thinking and
  cannot catch cross-step bugs. The integration test for a folder *is*
  that folder's most comprehensive usage example.
- **`tests/e2e/`: whole project → a handful of fat journey tests.** Each
  is a single function simulating an entire user session (chat → switch
  model → toggle plugin → interrupt → resume → branch …). ~10 journeys
  cover `{programmatic, HTTP+WS} × {creature, terrarium, studio}` +
  multi-node. e2e answers one question: *is the system runnable, end to
  end?*

Tier discipline: **behavior asserts, not shape asserts** (every mutation
test observes the side effect); **real collaborators, not mocks** (the
only seam is the LLM: `kohakuterrarium.testing.llm.ScriptedLLM`,
monkeypatched at BOTH `bootstrap.llm.create_llm_provider` and
`bootstrap.agent_init.create_llm_provider`); **to raise integration/e2e
coverage, fatten the existing workflow functions; do NOT add more test
functions.** Carve-out files (3rd-party providers, platform PTY,
end-user CLI/UI, the pywebview boot path) are listed in
`tests/README.md` and excluded from coverage targets.

Unit + integration run in CI on the full OS × Python matrix
(3.12+). **The e2e tier is NOT run in CI**: those tests spin up
real WebSocket-backed lab clusters, subprocess workers, and
Vue-frontend journey simulations whose timing depends on
hosted-runner network + scheduler behavior that's too volatile
to gate every PR on. Run e2e locally before shipping anything
that touches the multi-node / Studio / serving stack; bug
anchoring + regression protection on `main` come from unit +
integration.

## Core Architecture Concepts (CRITICAL)

### The four-layer hierarchy

```
User <-> Studio (management framework)
              |
              v  catalog / identity / sessions / persistence / editors / attach
         +-----------+
         | Terrarium |  <-- runtime engine: graph topology, channels,
         +-----------+      hot-plug, output wiring, session bookkeeping.
              |             No LLM, no reasoning loop. Owns structure.
              v
         | creature | creature | ... |  <-- the agent framework runs here
              |
              v
         | controller + LLM + tools + triggers + sub-agents + plugins + I/O |
```

**Studio** (`src/kohakuterrarium/studio/`): the management framework above the
engine. Six namespaces: `catalog`, `identity`, `sessions`, `persistence`,
`editors`, `attach`. The web dashboard, desktop app, and `kt` CLI are all
adapters over Studio. Studio is *not* a UI; it's the shared Python surface UIs
delegate to.

**Terrarium** (`src/kohakuterrarium/terrarium/engine.py`): the runtime engine
that hosts every running creature in the process. It runs no LLM and has no
reasoning loop; those live in the creatures it hosts. What it owns is
*structure*: which creatures share a connected component, which channels
exist between them, where each turn-end output is delivered, which session
store backs which graph, and the bookkeeping that follows when the topology
changes (auto-merge / auto-split, session lineage). One engine per process;
multiple disconnected graphs may coexist inside it.

**Creature**: dual concept. (1) Config: a folder with `agent.yaml` +
`system.md` defining an agent. (2) Runtime: a `Creature` handle
(`terrarium/creature_host.py`) wrapping a live `Agent` with engine-side
metadata (`graph_id`, `is_privileged`, `listen_channels`, `send_channels`,
`parent_creature_id`). Same agent config can run privileged in one terrarium
and unprivileged in another. Sub-agents inside a creature are VERTICAL
hierarchy (internal delegation, invisible to outside).

**Privileged node**: a creature inside a graph that has been granted the
[group tools](#privileged-tools-and-the-group_-surface) needed to mutate the
graph: spawn / remove creatures, draw / delete channels, start / stop
members. The recipe `root:` keyword is one way to make a node privileged;
recipes can also use `privileged: true` inline; engines accept
`is_privileged=True` at creature-add time. Workers spawned by `group_add_node`
are NOT privileged.

**Two composition levels (never mix them):**
- VERTICAL (inside creature): controller → sub-agents (private, hierarchical)
- HORIZONTAL (terrarium graph): creature ↔ creature via channels (peer, opaque)

### Dynamic graph + session interaction

Topology can change at runtime. The engine keeps it consistent:
- Add a creature → joins a specific graph (default: fresh singleton).
- Remove a creature → may auto-split the graph if it was a bridge.
- Connect across graphs → auto-merge graphs, union environments, merge
  session stores into one (with `parent_session_ids` recording lineage).
- Disconnect / remove channel → may auto-split, allocate fresh environments
  per side, duplicate session store into each side.
- Each graph has one session store; resume reconstructs topology from the
  recipe path stored in session metadata, NOT from a frozen snapshot. The
  recipe is the source of truth on resume; lineage metadata
  (`parent_session_ids`, `merged_at`, `split_at`) survives but split state
  does not.

### Privileged tools and the `group_*` surface

Tools registered on every creature: `send_channel`, `group_send`.

Tools registered ONLY on privileged nodes:
- `group_add_node`: spawn a creature into the caller's graph
- `group_remove_node`: remove a creature (may auto-split)
- `group_start_node` / `group_stop_node`: start / stop members
- `group_channel`: CRUD on channels and per-creature wiring
- `group_wire`: output-wiring edges
- `group_status`: snapshot the caller's graph

These are the runtime "graph editor": an LLM-driven privileged node uses
them to evolve the team mid-run. Mutations go through topology pure
functions (`terrarium/topology.py`) → environment updates → session
coordination → emit `EngineEvent`.

### Channels are broadcast-only at the graph layer

All terrarium graph channels are broadcast: every listener receives every
send. The `type:` field in older `terrarium.yaml` channel declarations is
ignored at the engine layer; new configs should omit it. The
`SubAgentChannel` queue primitive in `core/channel.py` still exists but is
internal to creature ↔ sub-agent plumbing, not user-facing.

### Built-in plugins (cross-cutting concerns are NOT framework features)

Four cross-cutting concerns ship as ordinary plugins, not framework code:
- `sandbox`: capability gating (filesystem / network / subprocess)
- `budget`: turn / tool-call / walltime accounting
- `permgate`: interactive user approval for tool calls
- `compact.auto`: trigger context compaction on high token use

The framework's tool executor knows nothing about any of these. They use
`pre_tool_execute` + `runtime_services` hooks like any other plugin. This
is the canonical example of where the framework / plugin boundary lives:
security, resource limits, and user gating are all *cross-cutting policies*,
not framework features. When designing new functionality, ask first whether
it could be a plugin instead; usually it should.

### Python API surface (post-2.0 redesign)

The programmatic entry points (see `examples/code/`):
- `await Agent.build(config, llm=, tools=, strict=True, …)`: canonical
  constructor; accepts a path, `@pkg/...` ref, or `AgentConfig`.
- Typed turns: `await agent.run(msg)` returns a `TurnResult`;
  `agent.run_stream(msg)` yields typed events (`TextChunk` / `Activity` /
  `TurnEnded`). The autonomous input-driven main loop is `run_forever()`;
  `run()` is no longer the loop.
- Strict-by-default typed errors: everything derives from
  `kohakuterrarium.errors.KTError` (subclasses also inherit the builtin
  exception the failure historically raised).
- Engine-owned sessions: `Terrarium(session_dir=...)` turns on autosession
  (one store per graph); `add_creature(session=...)` takes a path / `True` /
  `False` / `SessionStore`, so there is no manual store + attach ceremony.
- `@kohakuterrarium.tool` (`kt.tool`) wraps a plain function into a tool;
  pass instances via `tools=` to `Agent.build` / `add_creature`.
- `kohakuterrarium.packages` is a lazy public façade: `packages.ensure()`
  is the idempotent install, plus resolve / list / manifest helpers.
- Pre-flight validation: `kohakuterrarium.validate` (`config` / `llm` /
  `creature` / `ping`) raises typed errors loudly; `kt doctor` is the CLI
  wrapper over the same functions.

## Architecture Overview

### Key Design Principle: Controller as Orchestrator

**The controller's role is to dispatch tasks, not to do heavy work itself.**

- Controller outputs should be SHORT: tool calls, sub-agent dispatches, status updates
- Long outputs (user-facing content) should come from **output sub-agents**
- This keeps controller lightweight, fast, and focused on decision-making

### Five Major Systems
1. **Input** - Explicit input that triggers the agent (user request, ASR, group chat message)
2. **Trigger** - Automatic system that triggers agent (timers, events, conditions, composites)
3. **Controller** - Main LLM that **orchestrates** - dispatches tasks, makes decisions
4. **Tool Calling** - Background execution of tools/sub-agents (non-blocking)
5. **Output** - Final output routing (stdout, file, TTS stream, API)

### Unified Event Model

Everything flows through `TriggerEvent` (defined in `core/events.py`):
- Input completion → TriggerEvent
- Timer/condition triggers → TriggerEvent
- Tool completion → TriggerEvent
- Sub-agent output → TriggerEvent

Stackable events can be batched when occurring simultaneously.

### Key Concepts
- **Sub-agents**: Nested agents with own controller + tools
  - Default: output to parent controller only
  - **Output sub-agent**: `output_to: external` - can stream directly to user
  - **Interactive sub-agent**: `interactive: true` - stays alive, receives context updates
- **Skills**: Procedural knowledge ("how to do something")
- **Tools**: Executable functions with documentation ("how to call, what happens")
- **First-citizen memory**: Folder with txt/md files, read-write (some can be protected)
- **Plugins**: Hook-based extension layer (pre/post around tool calls, LLM calls, sub-agent runs, etc.)

### Tool Execution Modes
1. **Direct/Blocking**: Complete all jobs, return results
2. **Background**: Periodic status updates, context refresh
3. **Stateful**: Multi-turn interaction (like Python generators with yield)

## Configuration Format

- **JSON/YAML/TOML**: Overall setup (controller, input, trigger, tools, output modules)
- **Markdown**: System prompts with Jinja-like templating
- **Call syntax**: Configurable format (short, easy to parse, state-machine friendly)

## Project Structure

```
src/kohakuterrarium/
├── core/                     # Runtime engine
│   ├── agent.py              # Agent class: orchestrates everything (run_forever main loop)
│   ├── agent_construct.py    # Agent.build / Agent.from_path constructors (AgentConstructMixin)
│   ├── agent_turn.py         # Agent.run / Agent.run_stream: typed single-turn drivers
│   ├── turn.py               # TurnResult / TextChunk / Activity / TurnEnded: typed turn events
│   ├── agent_lifecycle.py    # Lifecycle helpers (AgentLifecycleMixin)
│   ├── agent_extensions.py   # Runtime injection: add_tool / add_plugin (AgentExtensionsMixin)
│   ├── agent_handlers.py     # Event handling, controller loop (AgentHandlersMixin)
│   ├── agent_tools.py        # Tool/subagent dispatch + bg completion (AgentToolsMixin)
│   ├── agent_messages.py     # Edit / regenerate / rewind past messages (AgentMessagesMixin)
│   ├── agent_model.py        # LLM-profile switching (AgentModelMixin)
│   ├── agent_mcp.py          # MCP init + prompt wiring for Agent
│   ├── agent_mid_turn.py     # Mid-turn user-input injection + interrupt-buffer drain
│   ├── agent_workspace.py    # Per-agent runtime working-directory controller
│   ├── agent_*.py            # Smaller agent helpers: runtime tools, compact-model glue,
│   │                         #   pre-dispatch plugin chain, native-tool / plugin option
│   │                         #   overrides, observability + tool metrics, budget recovery
│   ├── controller.py         # Controller: LLM conversation loop + event queue
│   │                         #   (+ controller_metrics.py, controller_plugins.py)
│   ├── conversation.py       # Context management (multimodal aware)
│   ├── compact.py            # Non-blocking context compaction (+ compact_text.py)
│   ├── config.py             # Config loading and parsing
│   ├── config_merge.py       # Inheritance/override merging for agent configs
│   ├── config_serde.py       # AgentConfig <-> primitive-dict (de)serialization
│   ├── config_types.py       # Config dataclasses (AgentConfig, InputConfig, etc.)
│   ├── constants.py          # Shared constants
│   ├── channel.py            # Channel primitives (SubAgentChannel, AgentChannel)
│   ├── events.py             # TriggerEvent + related event types
│   ├── executor.py           # Background job runner (+ job.py, single_flight.py,
│   │                         #   backgroundify.py mid-flight direct→background promotion)
│   ├── budget.py             # Shared budget primitives for agent / sub-agent loops
│   ├── tool_output.py        # Tool-output normalization + rendering
│   ├── metrics_hook.py       # Process-wide metrics observation hook
│   ├── native_tool_validation.py # Provider-native tool option validation
│   ├── loader.py             # Custom module loading from paths
│   ├── output_wiring.py      # Turn-end output wiring hook (routes to creature sinks)
│   ├── registry.py           # Per-agent module registration
│   ├── scratchpad.py         # Agent scratchpad state
│   ├── session.py            # Session reference (keyed shared state)
│   ├── environment.py        # Environment isolation for multi-agent
│   ├── termination.py        # Termination conditions
│   └── trigger_manager.py    # Runtime trigger management
│
├── bootstrap/                # Agent initialization factories
│   ├── agent_init.py         # Component initialization (AgentInitMixin)
│   ├── llm.py                # LLM provider creation
│   ├── tools.py              # Tool loading and registration
│   ├── triggers.py           # Trigger module creation
│   ├── subagents.py          # Sub-agent config loading
│   ├── io.py                 # Input/output module creation
│   └── plugins.py            # Plugin manager initialization
│
├── cli/                      # `kt` entry-point subcommands
│   ├── __init__.py           # main(): argparse + dispatch (+ _aliases, _config_layers,
│   │                         #   _aio_entrypoint)
│   ├── run.py                # kt run               : creature / recipe execution via the engine
│   ├── resume.py             # kt resume            : session resumption
│   ├── serve.py              # kt serve             : web API + frontend
│   ├── doctor.py             # kt doctor            : pre-flight env + config validation
│   ├── auth.py               # kt login             : provider authentication
│   ├── config.py             # kt config            : settings (LLM profiles, MCP registry,
│   │                         #   defaults, …) (+ config_prompts.py)
│   ├── identity_*.py         # settings backends (llm / keys / mcp / codex / settings / backend)
│   ├── extension.py          # kt extension         : plugin/extension management
│   ├── memory.py             # kt embedding / search: session memory
│   ├── model.py              # kt model             : profile management
│   ├── packages.py           # kt list/info/install/uninstall/edit
│   ├── marketplace.py        # kt marketplace list/add/remove/refresh/search/info
│   ├── admin.py              # admin-token / user administration (+ admin_qr.py)
│   ├── service.py            # multi-node service runner (+ lab_client.py)
│   ├── self_update.py        # launcher-managed self-update
│   └── version.py            # kt version
│
├── modules/                  # Plugin API for devs (extension protocols)
│   ├── input/                # Produces TriggerEvent(type="user_input")
│   ├── trigger/              # Produces TriggerEvent(type=...)
│   ├── tool/                 # On complete → TriggerEvent(type="tool_complete");
│   │                         #   function.py = @kohakuterrarium.tool function adapter
│   │                         #   doc_mode.py = brief / standard / full tier resolution
│   ├── output/               # State-machine router + output modules
│   ├── subagent/             # Sub-agent lifecycle management
│   │   ├── base.py           # SubAgent class (conversation loop)
│   │   ├── result.py         # SubAgentResult, SubAgentJob, framework hints
│   │   ├── manager.py        # SubAgentManager (spawn, cancel, cleanup)
│   │   ├── interactive.py    # InteractiveSubAgent (long-running)
│   │   ├── interactive_mgr.py# InteractiveManagerMixin
│   │   └── config.py         # SubAgentConfig dataclass
│   ├── user_command/         # User slash command protocol
│   └── plugin/               # Plugin protocol: pre/post hooks + callbacks
│       ├── base.py           # BasePlugin, PluginContext, PluginBlockError
│       └── manager.py        # PluginManager: runs hooks linearly by priority
│
├── builtins/                 # Built-in implementations
│   ├── tool_catalog.py       # Global builtin tool lookup (deferred loaders)
│   ├── subagent_catalog.py   # Global builtin sub-agent lookup
│   ├── plugin_catalog.py     # Global builtin plugin lookup
│   ├── tools/                # ~30 general tool classes (read, write, edit, multi_edit,
│   │                         # glob, grep, tree, bash, python, notebook_read/edit,
│   │                         # web_search, web_fetch, info, ask_user,
│   │                         # scratchpad_tool, send_message, stop_task, search_memory,
│   │                         # skill, image_gen, canvas_preview, show_card, …)
│   │                         #: group_* terrarium tools live in terrarium/tools_group*.py
│   ├── subagents/            # Built-in sub-agent configs
│   │                         # (coordinator, critic, explore, plan, research, response,
│   │                         #  summarize, worker, memory_read, memory_write)
│   ├── plugins/              # Built-in plugins (sandbox, budget, permgate, compact)
│   ├── inputs/               # cli, none
│   ├── outputs/              # stdout, tts, none
│   ├── user_commands/        # Slash commands (branch, channels, clear, compact, edit,
│   │                         # env, exit, fork, help, jobs, model, module, plugin,
│   │                         # regen, registry, scratchpad, …)
│   ├── cli_rich/             # Rich-based CLI UI (default `kt run` frontend)
│   │                         #: app, runtime, input, output, composer, completer,
│   │                         #   live_region, blocks/
│   └── tui/                  # Textual-based alternative TUI
│       ├── app.py            # AgentTUI Textual app
│       ├── input.py          # TUIInput module
│       ├── output.py         # TUIOutput module
│       ├── session.py        # TUISession shared state
│       └── widgets/          # Widget subpackage (blocks, messages, panels, input, modals)
│
├── builtin_skills/           # Markdown skill manifests for on-demand tool/subagent docs
│   ├── tools/                # One .md per built-in tool
│   └── subagents/            # One .md per built-in sub-agent
│
├── llm/                      # LLM abstraction
│   ├── base.py               # LLMProvider protocol
│   ├── openai.py             # OpenAI-compatible provider (also OpenRouter)
│   │                         #   (+ openai_helpers.py, openai_sanitize.py, openai_ws.py)
│   ├── responses_ws.py       # Responses-API WebSocket session (incremental
│   │                         #   previous_response_id continuation; openai + codex)
│   ├── anthropic_provider.py # Native Anthropic Messages API provider (official SDK)
│   │                         #   (+ anthropic_format.py, anthropic_pairing.py, anthropic_cache.py)
│   ├── codex_provider.py     # Codex OAuth provider (ChatGPT-subscription)
│   │                         #   (+ codex_auth.py, codex_format.py, codex_image_gen.py,
│   │                         #      codex_rate_limits.py)
│   ├── litellm_provider.py   # LiteLLM provider (optional dep)
│   ├── deferred_provider.py  # Placeholder provider for "no model configured yet"
│   ├── message.py            # Message types (multimodal-aware ContentPart, etc.)
│   ├── tools.py              # Native tool schema builders (+ tool_schemas.py)
│   ├── presets.py            # 50+ model presets (pure data) (+ preset_aliases.py,
│   │                         #   preset_store.py)
│   ├── backends.py           # Backend (provider) persistence: YAML store shared with presets
│   ├── profile_types.py      # LLMBackend / LLMPreset / LLMProfile dataclasses
│   ├── profiles.py           # Profile resolution + management
│   ├── variations.py         # `name@group=option` variation selectors
│   ├── recovery.py           # Provider-boundary recovery helpers
│   └── api_keys.py           # API key storage/retrieval
│
├── prompt/                   # Prompt assembly and templating
│   ├── aggregator.py         # Gated composition of every framework section
│   ├── framework_hints.py    # Six canonical, overrideable prose blocks
│   ├── loader.py             # Loads prompt files / inline strings
│   ├── tool_contributions.py # Tool-supplied prompt guidance fragments
│   ├── skill_loader.py       # On-demand built-in skill loading
│   └── template.py           # Jinja-like templating
│
├── parsing/                  # Stream parsing (state machine over LLM output)
│   ├── state_machine.py      # StreamParser: extracts tool calls / commands / text
│   ├── patterns.py           # Marker regexes
│   ├── events.py             # ToolCallEvent, CommandEvent, TextEvent, …
│   └── format.py             # ToolCallFormat enum (bracket, xml)
│
├── commands/                 # Framework commands (##info##, ##read##)
│
├── session/                  # Session persistence (KohakuVault-backed)
│   ├── store.py              # SessionStore: meta/state/events/channels/subagents/jobs/conversation/fts
│   │                         #   (+ store_counters.py, store_protocol.py, token_views.py,
│   │                         #      version.py, errors.py, migrations/)
│   ├── store_fork.py         # Fork / branch primitive for SessionStore
│   ├── reader.py             # SessionReader: read a finished .kohakutr without spelunking
│   ├── output.py             # SessionOutput: captures events via OutputModule
│   ├── resume.py             # Resume agent/terrarium from .kohakutr file
│   ├── attach.py             # Attach/detach a store to a live agent
│   │                         #   (+ agent_attach.py, attachment_service.py)
│   ├── artifacts.py          # Session-local artifact helpers
│   ├── session.py            # Async wrapper around a running agent + SessionStore
│   ├── rollup.py             # Per-turn rollup helpers
│   ├── sync.py               # Event mirroring across the Laboratory layer
│   ├── memory.py             # SessionMemory: FTS5 + vector search over events
│   ├── embedding.py          # Embedding providers (model2vec, sentence-transformer, API)
│   └── history.py            # Event-history normalization
│
├── serving/                  # Web/desktop launch glue (no service manager: Studio/Terrarium
│   │                         #   own lifecycle; AgentSession / KohakuManager are gone)
│   ├── web.py                # Static web frontend serving + pywebview desktop app
│   ├── events.py             # Legacy serving event dataclasses
│   └── process_metrics.py    # Process-wide metrics aggregator (canonical subscriber)
│
├── terrarium/                # Terrarium runtime engine (multi-agent graphs)
│   ├── engine.py             # Terrarium engine: graph/session lifecycle, add/remove
│   │                         #   creatures, connect/disconnect, subscribe, output wiring
│   ├── creature_host.py      # Creature handle: chat(), inject_input(), graph metadata
│   ├── creature.py           # CreatureHandle dataclass (config-level wrapper)
│   ├── creature_ops.py       # Pure agent-touching helpers shared with studio.sessions
│   ├── topology.py           # Pure-data graph/channel topology + merge/split deltas
│   │                         #   (+ topology_snapshot.py runtime snapshot/replay)
│   ├── events.py             # EngineEvent / EventKind / EventFilter observable surface
│   ├── channels.py           # Channel layer for the engine (+ channel_lifecycle.py)
│   ├── autosession.py        # Engine-owned session persistence (session_dir autosession)
│   ├── session_coord.py      # Session merge/split coordination across graphs
│   ├── recipe.py             # Apply a TerrariumConfig recipe to an engine
│   ├── resume.py             # Engine-level resume: adopt a saved session
│   ├── root.py               # assign_root: privileged-node promotion + root wiring
│   ├── runtime_prompt.py     # Keeps creature system prompts in sync with the group
│   ├── tools_group*.py       # group_* tool surface (lifecycle, channel, send, status,
│   │                         #   wire + shared helpers, hooks, caller context)
│   ├── multi_node_*.py       # Multi-node service: cluster fold, routing, replication,
│   │                         #   channels (lab-host mode)
│   ├── service.py            # TerrariumService: runtime abstraction Studio depends on
│   ├── remote_service.py     # TerrariumService backed by Lab APP calls
│   ├── wire.py               # Pack/unpack DTO helpers for Laboratory APP transport
│   ├── wiring.py             # Output-wiring helpers (+ output_wiring.py resolver)
│   ├── observer.py           # ChannelObserver for non-destructive monitoring
│   ├── output_log.py         # Capture and log creature output
│   ├── config.py             # Terrarium config loading + topology prompt
│   ├── engine_cli.py         # Engine TUI launcher (kt run full-screen mode)
│   ├── engine_rich_cli.py    # Engine rich inline-CLI launcher (--mode cli)
│   └── cli_output.py         # CLIOutput for headless mode
│
├── api/                      # FastAPI HTTP API (in-package)
│   ├── app.py                # FastAPI factory + middleware + lifespan
│   ├── main.py               # CLI entry point (default port 8001)
│   ├── deps.py               # Dependency injection (per-user routing via engine pool)
│   ├── schemas.py            # Pydantic request/response models
│   ├── events.py             # Shared event log + StreamOutput
│   ├── routes/               # REST endpoints grouped by Studio namespace (catalog/,
│   │                         #   identity/, sessions_v2/, persistence/, attach/, plus
│   │                         #   app_update, health, metrics, runtime_graph, lab_*)
│   ├── ws/                   # WebSocket handlers (io, files, logs, daemon_logs,
│   │                         #   observer, pty, runtime_graph, trace, memory_build)
│   └── auth/                 # Four-layer auth: capabilities, L2 host token
│                             # middleware, L3 admin Depends, L4 user accounts +
│                             # engine pool. Strictly API-server-scoped; nothing
│                             # below api/ imports from here (dep-graph guard).
│
├── compose/                  # Pythonic agent-composition algebra
│   ├── core.py               # BaseRunnable + Sequence/Product/Fallback/Retry/Router
│   └── agent.py              # AgentRunnable, AgentFactory (engine-backed)
│   # Operators: a >> b (sequence), a & b (parallel), a | b (fallback), a * N (retry)
│   # Imported by user code only: nothing inside the framework imports it.
│
├── mcp/                      # Model Context Protocol client integration
│   ├── client.py             # MCPClientManager, MCPServerConfig, MCPServerInfo
│   └── tools.py              # Four meta-tools: mcp_list / mcp_call / mcp_connect / mcp_disconnect
│   # MCP tools are NOT injected into the agent's tool list: the agent calls them
│   # via the four meta-tools, keeping the system prompt small even with many MCP servers.
│
├── testing/                  # Test infrastructure
│   ├── llm.py                # ScriptedLLM: deterministic mock (+ fake_llm_provider.py)
│   ├── output.py             # OutputRecorder: capture for assertions
│   ├── events.py             # EventRecorder: timing assertions
│   ├── agent.py              # TestAgentBuilder: test harness
│   └── terrarium.py          # Engine test helpers (+ subprocess_seam.py)
│
├── utils/                    # Shared utilities (logging, async helpers, config_dir,
│                             #   file_guard, file_walk, mobile_sandbox)
│
├── packages/                 # Package install / resolve / git-backend / marketplace
│   ├── __init__.py           # Lazy public façade (PEP 562): packages.ensure(),
│   │                         #   install / resolve / list re-exports for user code
│   ├── install.py            # install / update / uninstall + ensure() idempotent install
│   ├── manifest.py           # kohaku.yaml parsing + python_dependencies installer
│   ├── locations.py          # packages_dir() (honours KT_CONFIG_DIR) + .link helpers
│   ├── resolve.py            # @<pkg>/<sub/path> → absolute Path (installed packages)
│   ├── slots.py              # Extra manifest slots (skills / commands / prompts)
│   ├── walk.py               # list_packages (installed scan)
│   ├── git_backend.py        # native git + dulwich fallback (Android)
│   ├── marketplace.py        # TerrariumMarket resolver (fetch + cache + search)
│   └── marketplace_types.py  # Frozen dataclasses + typed errors
├── launcher/                 # Thin Briefcase wrapper: owns the managed venv +
│                             # self-update flow.  STRICT BOUNDARY: launcher/*.py
│                             # imports nothing from kohakuterrarium.<not launcher>
│                             # (enforced by tests/unit/test_dep_graph_lint.py).
│                             # See plans/1.5.0-roadmap/06-app-update/ for design.
├── errors.py                 # Typed exception hierarchy: KTError base; bottom of the
│                             #   dep graph (imports nothing from kohakuterrarium)
├── validate.py               # kt.validate.config/llm/creature/ping: pre-flight checks
│                             #   that raise typed errors; `kt doctor` wraps these
├── __briefcase__.py          # Briefcase desktop entry: delegates to launcher.main
├── app_icon.{ico,icns,png}   # Desktop app icons
└── web_dist/                 # Built Vue frontend (output of `npm run build`)
```

## Major Systems

1. **Agent runtime** (`core/`): Turn-based LLM controller, async non-blocking tool execution, unified TriggerEvent queue, sub-agent dispatch.
2. **Multi-agent orchestration** (`terrarium/`): The `Terrarium` runtime engine. Channels between creatures, hot-plug with auto merge/split, optional privileged node carrying the `group_*` management tools.
3. **Session persistence** (`session/`): `.kohakutr` files via KohakuVault (SQLite). Append-only event log + conversation snapshots + sub-agent capture + channel history + scratchpad. Resume via `kt resume`. Listing + search are backed by a sidecar SQLite cache at `<session_dir>/.kt-index.kvault` (`studio/persistence/session_index/`): one file open + FTS5 BM25 query for the whole list endpoint regardless of session count; incremental reconcile via `(mtime,size)` fingerprint diff so unchanged files skip the per-session SQLite open.
4. **Memory** (`session/memory.py` + `session/embedding.py`): FTS5 + vector search over recorded events. Embedding via model2vec / sentence-transformer / API providers.
5. **HTTP API + Web dashboard** (`api/` + `src/kohakuterrarium-frontend/`): FastAPI REST + WebSocket. Vue 3 frontend served from `web_dist/`. Multi-tab chat, tool accordion, session resume.
6. **Plugin system** (`modules/plugin/`): Pre/post hooks around tool execution, LLM calls, sub-agent runs, plus fire-and-forget callbacks. `PluginBlockError` in a `pre_tool_execute` becomes the tool result. All plugins run linearly by priority.
7. **MCP integration** (`mcp/`): Stdio + HTTP transport. Tools indirected through four meta-tools instead of mirrored, which keeps the agent's prompt small.
8. **Compose algebra** (`compose/`): `>>` sequence, `&` parallel, `|` fallback, `*` retry. User-facing only; framework does not depend on it.
9. **Package system** (`packages/` + `kt install` / `kt uninstall`): Sharing creature / terrarium / plugin bundles.  Marketplace integration (`packages/marketplace.py` + `kt marketplace …` + `/api/catalog/marketplace/*`) resolves `@<name>` install specs against [TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket) (default source; user-configurable list at `~/.kohakuterrarium/marketplace-sources.json`).  Cache at `~/.kohakuterrarium/marketplace/cache.json` (1h TTL, ETag-revalidated, `KT_MARKETPLACE_CACHE_TTL` overridable, `KT_MARKETPLACE_SOURCES` env override).  Frontend Settings → Extensions tab is now a two-pane Catalog view (Browse + Installed) backed by `stores/marketplace.js` + `utils/marketplaceApi.js`.
10. **Desktop packaging** (`__briefcase__.py` + briefcase tooling): macOS / Windows / Linux native app builds.
11. **Auth** (`api/auth/`): four optional layers stacked at the API server: L1 host selection (frontend), L2 host token (middleware), L3 admin token (FastAPI Depends on config-mutating routes), L4 user accounts (sqlite + per-user `Terrarium` engine pool). Defaults to OFF; see `plans/1.5.0-roadmap/03-frontend-backend-connection/` + `docs/{en,zh-CN,zh-TW}/guides/authentication.md`.

## Auth invariant (CRITICAL)

**Auth lives entirely in `src/kohakuterrarium/api/auth/`.  Nothing
below `api/` knows about users / tokens / hosts.**  When L4 (multi-user)
is on, per-user isolation is achieved by routing each authenticated
request to a per-user `Terrarium` from the engine pool; the engine
itself stays single-tenant.  CLI / TUI / `kt run` paths construct a
`Terrarium` directly and run unauthenticated; only the FastAPI server
multiplexes.

A dep-graph guard enforces `from kohakuterrarium.api.auth.*` cannot
appear outside `src/kohakuterrarium/api/`.  This isolation parallels
the launcher's strict-isolation rule.

## Plugin System

`modules/plugin/` defines two extension patterns:

- **Pre/post hooks**: wrap framework methods. Pre-hooks can transform input or block (`PluginBlockError`); post-hooks can transform output. Hooks are linear (not nested) by priority. Returning `None` keeps the value unchanged.
- **Callbacks**: fire-and-forget notifications.

Plugin context (`PluginContext`) exposes: `agent_name`, `working_dir`, `session_id`, `model`, `switch_model()`, `inject_event()`, plus plugin-scoped `get_state()` / `set_state()` persisted to the session store.

Loaded by `bootstrap/plugins.py`. Manager calls live in `core/agent_handlers.py` (pre-LLM) and `core/agent_tools.py` (pre/post tool, pre/post sub-agent).

## MCP Integration

`mcp/client.py` owns per-server `ClientSession`s for stdio and streamable-HTTP MCP servers. `MCPClientManager` is attached to `Agent._mcp_manager` when the agent config declares `mcp_servers`. The `mcp` SDK is a deferred import inside `connect()`, so frameworks without it installed start fine.

The agent does **not** see MCP tools as native tools. Instead, four meta-tools (`mcp_list`, `mcp_call`, `mcp_connect`, `mcp_disconnect`) route to the manager. This keeps the system prompt short regardless of how many MCP servers the user attaches, and contains MCP failures to a single tool call.

## Compose Algebra

Pythonic operators over engine-backed creatures and arbitrary callables. Lives in `compose/`:

```python
pipeline = explorer >> (planner & critic) >> writer
result = await (pipeline | fallback) * 3
```

| Op | Combinator | Semantics |
|----|------------|-----------|
| `a >> b` | `Sequence` | Run `a`, pipe output to `b`. |
| `a & b` | `Product`  | Run concurrently, return tuple. |
| `a \| b` | `Fallback` | Try `a`; on exception, run `b` with the original input. |
| `a * N`  | `Retry`    | Retry `a` up to `N` times. |

Pure async combinators with zero framework coupling beyond `terrarium/`. Nothing inside the framework imports `compose/`.

## Prompt System Design (CRITICAL - MUST FOLLOW)

### System Prompt Aggregation

Two payloads reach the model each turn: the assembled system prompt and the
native tool schemas. For a 20-callable creature the schemas are the larger half,
so prompt-size work that ignores `llm/tools.py` is optimizing the smaller one.

`prompt/aggregator.py` composes the prompt from **gated** sections. The rule:
**a block whose subject does not exist is not emitted.** An inapplicable block
is worse than a missing one — it teaches the model something false.

1. **Base prompt from system.md** — always. Personality and domain judgment only.
2. **`## Available Functions`** — only when `tool_format != "native"` or
   `tool_doc_mode == "full"`. Native providers already carry the inventory.
3. **`## Function Documentation`** — only for tools resolved to `full`.
4. **`## Tool guidance`** — `BaseTool.prompt_contribution()`, cache-stable order.
5. **Plugin contributions** — `modules/plugin/manager.py`.
6. **`## Skills`** — byte-budgeted procedural-skill index.
7. **`## Working with the group`** / **`## Growing the group`** — injected by
   `terrarium/runtime_prompt.py` only when the creature is actually in a graph /
   is privileged. A solo creature costs zero bytes for both.
8. **`## Untrusted content`**, **`## Calling functions`** (text formats only),
   **`## Output format`** (text formats only), **`## Execution model`**.

### Documentation tiers

`tool_doc_mode` is `brief` | `standard` (default) | `full`, overridable per tool
with `doc_mode` on a `tools:` entry. Files in `builtin_skills/` split at
`## Reference`: above it is the **usage** tier that `full` inlines, below it is
reachable only through `info`, in every mode.

### What Goes Where

| Content | Location |
|---------|----------|
| Agent personality / role / domain judgment | `system.md` |
| What is true about the runtime (dispatch, channels, graph) | framework blocks |
| Tool name + one-line description (≤160 chars, with a "Not for" clause) | tool class, mirrored in the doc frontmatter |
| Argument tables, behavior, limits | `builtin_skills/<tool>.md` above `## Reference` |
| Output formats, edge cases, worked failures | below `## Reference` |
| A procedure for a task | a skill, not a creature prompt |

### NEVER Do These

1. **NEVER put a tool list in system.md** — it is auto-aggregated or carried as schema.
2. **NEVER put tool call syntax in system.md or a tool doc** — examples are
   generated from the active format, and native creatures get none.
3. **NEVER repeat framework semantics per tool** — `run_in_background` appears
   only on tools declaring `supports_background`; sub-agent isolation is stated
   once in `## Execution model`.
4. **NEVER hardcode a tool description** — it must equal its doc frontmatter;
   `tests/unit/test_tool_doc_shape.py` enforces that.
5. **NEVER add a framework section without asserting its gate in both
   directions** — present when true, absent when false.

### Budget

`tests/integration/test_prompt_budget.py` fails the build if the framework
payload for the reference creature exceeds its target, and
`tests/unit/test_tool_doc_shape.py` enforces the tier caps and the
schema ⟷ doc bijection. Design detail lives in `plans/sys-prompt-opt/`.

## Tool Execution Design (CRITICAL - MUST FOLLOW)

### Async Non-Blocking Execution

Tool execution follows this flow:
1. **During LLM streaming**: When `##tool##` block detected, start tool immediately via `asyncio.create_task()`
2. **Don't block streaming**: LLM continues outputting while tools run in background
3. **Parallel execution**: Multiple tools run simultaneously
4. **After streaming ends**: Wait for all direct tools with `asyncio.gather()`
5. **Batch results**: Combine all results into single event for controller

### NEVER Do These

1. **NEVER queue tools until LLM finishes**: start immediately when detected
2. **NEVER execute tools sequentially**: run in parallel with `gather()`
3. **NEVER block LLM output for tool execution**: they run concurrently

### Tool Execution Modes

- **Direct/Blocking**: All jobs complete before returning (default for SWE agents)
- **Background**: Periodic status updates, context refresh
- **Stateful**: Multi-turn interaction (sub-agents)

## Session System

Sessions store everything in a `.kohakutr` file (SQLite via KohakuVault):
- Conversation snapshots (raw message dicts via msgpack, preserves tool_calls)
- Append-only event log (every text chunk, tool call, trigger, token usage)
- Sub-agent conversation capture (saved before destruction)
- Channel message history
- Scratchpad state
- Plugin-scoped state (`plugin:<name>:<key>`)

Resume rebuilds the agent from config and injects the saved conversation.

Key files: `session/store.py`, `session/output.py`, `session/resume.py`, `session/history.py`.

## CI Matrix

CI is defined in `.github/workflows/ci.yml`. PRs are not reviewed until CI is green on the contributor's fork. The matrix:

- **Lint**: `ruff check src/ tests/` + `black --check src/ tests/` (Python 3.13)
- **Tests**: `pytest tests/unit/` then `pytest tests/integration/`. CI runs the unit + integration tiers only, on Python 3.12, 3.13, 3.14 × Linux / Windows / macOS (3.14 on Windows excluded because pythonnet has no wheel). The e2e tier is intentionally NOT run in CI; see `tests/README.md` (run it locally before shipping multi-node / Studio / serving changes). Python 3.10 / 3.11 still install via `requires-python = ">=3.10"` but are supported best-effort; CI does not validate them.
- **File-size guards**: `pytest tests/unit/test_file_sizes.py`
- **Frontend**: `npm ci` + `npm run format:check` + `npm run build` in `src/kohakuterrarium-frontend/`, plus check that build output landed in `src/kohakuterrarium/web_dist/`
- **Wheel build**: build wheel, install into clean venv, run `kt --help` and `kt app --help`

Local pre-flight commands and the contribution policy are in [`CONTRIBUTING.md`](CONTRIBUTING.md).
