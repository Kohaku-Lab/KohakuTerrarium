# AGENTS.md — Guide for AI Agents Working With This Repo

This file tells an AI agent everything it needs to operate on, develop for, or
extend KohakuTerrarium. Split into two halves: **user-facing** (running,
configuring, and extending as a user) and **developer-facing** (contributing to
the framework itself).

---

## Part 1 — User-Facing

### 1. What Is KohakuTerrarium?

A **framework for building agents** — not another agent. The core abstraction
is the **creature**: a standalone agent with its own controller, tools,
sub-agents, triggers, memory, and I/O. Creatures compose horizontally into a
**terrarium** (pure wiring layer, no LLM). Everything is async Python.

The entry point is the `kt` CLI.

### 2. Installation

```bash
# From PyPI (stable)
pip install kohakuterrarium           # core
pip install "kohakuterrarium[full]"   # everything including torch embeddings

# From source (editable, for development)
git clone --recurse-submodules https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"

# Build the web frontend (required for kt web / kt app from source)
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

Verify: `kt --version`

### 3. Authentication

```bash
# Option A: Codex OAuth (ChatGPT subscription, no API key needed)
kt login codex
kt model default gpt-5.4

# Option B: Any OpenAI-compatible provider
kt config key set openai              # or anthropic, openrouter, gemini
kt config llm add                     # interactive preset builder
kt model default <preset-name>
```

Supported backends: OpenRouter, OpenAI, Anthropic, Google Gemini, any
OpenAI-compatible API, and Codex OAuth.

### 4. Installing Packages (Creatures, Plugins, Terrariums)

```bash
# Official showcase pack (SWE, reviewer, researcher, ops, creative, general, root)
kt install https://github.com/Kohaku-Lab/kt-biome.git

# Any third-party package
kt install <git-url>
kt install ./my-creatures -e          # editable local install

# Manage
kt list                               # show installed packages
kt info @kt-biome/creatures/swe       # inspect a creature config
kt update kt-biome                     # pull latest
kt uninstall kt-biome                  # remove
```

Packages live at `~/.kohakuterrarium/packages/<name>/`. Reference anything
inside with `@<package>/path`.

### 5. Running Agents

#### Single creature

```bash
kt run @kt-biome/creatures/swe --mode cli     # Rich inline (default on TTY)
kt run @kt-biome/creatures/swe --mode tui     # Full-screen Textual app
kt run @kt-biome/creatures/swe --mode plain   # Bare stdout, for piping/CI
kt run @kt-biome/creatures/swe --llm claude-opus-4.7   # override model
kt run ./my-creature                           # local path
```

`Ctrl+C` exits cleanly and prints a `kt resume` hint.

#### Multi-agent terrarium

```bash
kt terrarium run @kt-biome/terrariums/swe_team --mode tui
kt terrarium run ./my-terrarium --seed "Fix the auth bug"
```

TUI mode gives you multi-tab view: root agent + each creature + each channel.

#### Web dashboard and desktop app

```bash
kt serve start                        # detached daemon (outlives terminal)
kt web                                # foreground web server at 127.0.0.1:8001
kt app                                # native desktop window via pywebview
```

### 6. Session Persistence and Resume

Every run creates a `.kohakutr` file (SQLite) under
`~/.kohakuterrarium/sessions/`. It stores: conversation snapshots, event log,
sub-agent conversations, channel history, scratchpad, jobs, triggers, config
snapshot, and binary artifacts.

```bash
kt resume --last                      # most recent session
kt resume                             # interactive picker (10 most recent)
kt resume my-agent_20240101           # by name prefix
kt resume ~/backup/run.kohakutr       # full path
kt resume --llm gpt-5.4              # override model on resume
```

Resume auto-detects agent vs terrarium from the session file.

### 7. Memory and Search

```bash
# Build an embedding index over a session
kt embedding ~/.kohakuterrarium/sessions/swe.kohakutr
kt embedding swe.kohakutr --provider sentence-transformer --model @best

# Search from CLI
kt search swe "auth bug"              # auto (hybrid if vectors exist, else fts)
kt search swe "auth bug" --mode fts   # keyword only
kt search swe "auth bug" --mode semantic
```

Embedding providers: `model2vec` (default, no torch), `sentence-transformer`
(needs torch), `api` (remote), `auto` (picks best available).

Agents can search their own memory at runtime via the `search_memory` tool.

### 8. MCP (Model Context Protocol) Integration

Declare MCP servers per-agent or globally:

```yaml
# Per-agent in config.yaml
mcp_servers:
  - name: sqlite
    transport: stdio
    command: mcp-server-sqlite
    args: ["/var/db/my.db"]
  - name: docs_api
    transport: http
    url: https://mcp.example.com/sse
```

```bash
# Global (all agents)
kt config mcp list
kt config mcp add                     # interactive
kt config mcp edit sqlite
kt config mcp delete sqlite
```

MCP tools are surfaced through four meta-tools (`mcp_list`, `mcp_call`,
`mcp_connect`, `mcp_disconnect`) — keeps the system prompt small regardless
of how many servers are connected.

### 9. LLM Profile Management

```bash
kt config llm add                     # interactive preset builder
kt config llm list                    # show configured profiles
kt model default <preset-name>        # set default model
kt config provider add <name>         # add an OpenAI-compatible provider
kt config provider list               # show providers
kt config key set <provider>          # set API key
```

### 10. Configuration Reference

A creature lives in a folder with `config.yaml` (or `.yml`/`.json`/`.toml`):

```yaml
name: my-agent
base_config: "@kt-biome/creatures/swe"   # inherit from existing creature
controller:
  llm: claude-opus-4.7
  reasoning_effort: high
system_prompt_file: prompts/system.md     # personality + guidelines ONLY
tools:
  - read
  - write
  - bash
  - name: my_custom_tool
    type: custom
    module: ./tools/my_tool.py
subagents:
  - explore
  - plan
  - worker
plugins:
  - name: my_guard
    type: custom
    module: ./plugins/my_guard.py
mcp_servers: [...]
triggers: [...]
input:
  type: cli                              # or tui, none, custom, package
output:
  type: stdout                           # or tts, custom
memory:
  folder: memory/
compact:
  enabled: true
```

**What goes where:**
| Content | Location |
|---------|----------|
| Agent personality / role | `system.md` |
| Agent-specific guidelines | `system.md` |
| Tool list (name + description) | **AUTO-GENERATED** — never put in system.md |
| Tool call syntax | **AUTO-GENERATED** — in framework hints |
| Full tool documentation | `##info <tool_name>##` on-demand |

**NEVER** put tool lists, tool call syntax, or full tool docs in `system.md`.

### 11. Inheritance

Creatures inherit from other creatures via `base_config`:

- **Scalars**: child wins
- **Dicts** (`controller`, `input`, `output`, etc.): shallow merge
- **Identity-keyed lists** (`tools`, `subagents`, `plugins`): union by `name`; on collision, child wins
- **Prompt files**: concatenate along the chain; inline prompt appended last
- `no_inherit: [tools, plugins]` drops inherited fields entirely

### 12. Built-in Tools

| Category | Tools |
|----------|-------|
| Shell | `bash`, `python` |
| File ops | `read`, `write`, `edit`, `multi_edit`, `glob`, `grep`, `tree` |
| Structured data | `json_read`, `json_write` |
| Web | `web_fetch`, `web_search` |
| Interactive/Memory | `ask_user`, `think`, `scratchpad`, `search_memory` |
| Communication | `send_message` |
| Introspection | `info`, `stop_task` |
| Triggers | `add_timer`, `add_schedule`, `watch_channel` (type: trigger) |
| Terrarium (root-only) | `terrarium_create`, `terrarium_send`, `creature_start`, `creature_stop` |
| Media | `image_gen` (provider-native) |

### 13. Built-in Sub-agents

| Name | Purpose |
|------|---------|
| `worker` | Fix bugs, refactor, run validations |
| `coordinator` | Decompose, dispatch, aggregate |
| `explore` | Read-only codebase exploration |
| `plan` | Read-only planning |
| `research` | External web research |
| `critic` | Code review |
| `response` | User-facing copy generation (typically `output_to: external`) |
| `memory_read` / `memory_write` | Recall / persist findings to memory folder |
| `summarize` | Condense conversation for handoff or reset |

### 14. Plugins

Two flavours:

1. **Prompt plugins** — contribute to the system prompt via `get_content(context)`.
2. **Lifecycle plugins** — hook into: `on_load`, `on_unload`, `pre_llm_call`,
   `post_llm_call`, `pre_tool_dispatch`, `pre_tool_execute`,
   `post_tool_execute`, `pre_subagent_run`, `post_subagent_run`, and more.

`PluginBlockError` raised in a pre-hook replaces the call result (blocking it).

Plugin context exposes: `agent_name`, `working_dir`, `session_id`, `model`,
`switch_model()`, `inject_event()`, plugin-scoped `get_state()`/`set_state()`.

### 15. Programmatic Usage (Python Embedding)

```python
import asyncio
from kohakuterrarium.core.agent import Agent

async def main():
    agent = Agent.from_path("@kt-biome/creatures/swe")
    agent.set_output_handler(lambda text: print(text, end=""), replace_default=True)
    await agent.start()
    await agent.inject_input("Explain what this codebase does.")
    await agent.stop()

asyncio.run(main())
```

**Composition algebra** (user-facing only, framework does not depend on it):

```python
from kohakuterrarium.compose import agent, factory
pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer
result = await (pipeline | fallback) * 3    # sequence | fallback * retry
```

| Operator | Meaning |
|----------|---------|
| `a >> b` | Sequence: run `a`, pipe output to `b` |
| `a & b` | Parallel: run concurrently, return tuple |
| `a \| b` | Fallback: try `a`, on exception run `b` |
| `a * N` | Retry: retry up to N times |

### 16. Terrarium Config

```yaml
terrarium:
  name: swe-team
  root:                                         # optional, sits OUTSIDE
    base_config: "@kt-biome/creatures/general"
    system_prompt_file: prompts/root.md
  creatures:
    - name: swe
      base_config: "@kt-biome/creatures/swe"
      output_wiring: [reviewer]                 # deterministic pipeline edge
      channels:
        listen: [tasks, feedback]
        can_send: [status]
    - name: reviewer
      base_config: "@kt-biome/creatures/swe"
      system_prompt_file: prompts/reviewer.md
      channels:
        listen: [status]
        can_send: [feedback, results, status]
  channels:
    tasks:    { type: queue }       # one consumer per message
    feedback: { type: queue }
    results:  { type: queue }
    status:   { type: broadcast }   # all subscribers get every message
```

Auto-created channels: one `queue` per creature (named after it for DMs) and
a `report_to_root` queue if root is set.

### 17. Quick Reference: CLI Commands

| Command | Purpose |
|---------|---------|
| `kt run <path>` | Run a single creature |
| `kt resume [session]` | Resume a prior session |
| `kt terrarium run <path>` | Run a multi-agent terrarium |
| `kt install <source>` | Install a package |
| `kt list` | List installed packages |
| `kt info <path>` | Inspect a creature config |
| `kt login <provider>` | Authenticate (codex, etc.) |
| `kt config ...` | Settings (LLM, providers, keys, MCP) |
| `kt model ...` | Profile management |
| `kt embedding <session>` | Build vector index |
| `kt search <session> <query>` | Search session memory |
| `kt serve start/stop/status/logs` | Web API daemon |
| `kt web` | Foreground web server |
| `kt app` | Desktop app |
| `kt extension ...` | Plugin/extension management |
| `kt mcp ...` | MCP client tooling |
| `kt version` | Version info |

---

## Part 2 — Agent/Developer-Facing

### 1. Repository Structure

```
KohakuTerrarium/
├── src/kohakuterrarium/          # Framework source
│   ├── core/                     # Runtime engine (agent, controller, executor, events, channels)
│   ├── bootstrap/                # Agent initialization factories
│   ├── cli/                      # `kt` entry point and subcommands
│   ├── modules/                  # Plugin protocols (input, trigger, tool, output, subagent, plugin)
│   ├── builtins/                 # Built-in tools, sub-agents, inputs, outputs, TUI, slash commands
│   ├── builtin_skills/           # On-demand markdown docs for tools/sub-agents
│   ├── llm/                      # LLM provider abstraction + presets + profiles
│   ├── prompt/                   # Prompt aggregation and templating
│   ├── parsing/                  # Stream parser (state machine for tool-call detection)
│   ├── commands/                 # Framework commands (##info##, ##read##)
│   ├── session/                  # Session persistence (.kohakutr files via KohakuVault)
│   ├── serving/                  # Transport-agnostic agent/terrarium serving layer
│   ├── terrarium/                # Multi-agent runtime
│   ├── api/                      # FastAPI HTTP API + WebSocket
│   ├── compose/                  # Pythonic agent-composition algebra (>> & | *)
│   ├── mcp/                      # MCP client integration
│   ├── testing/                  # Test infrastructure (ScriptedLLM, recorders, harness)
│   ├── utils/                    # Shared utilities
│   ├── packages.py               # Package manager
│   └── web_dist/                 # Built Vue frontend output
├── src/kohakuterrarium-frontend/ # Vue 3 + Vite frontend (JS only, no TypeScript)
├── tests/                        # Unit + integration tests
├── examples/                     # Example agent-apps, terrariums, plugins, code samples
├── docs/                         # Documentation (en, zh-CN, zh-TW)
├── scripts/                      # Dev scripts
├── CLAUDE.md                     # Architecture rules and code conventions (authoritative)
├── CONTRIBUTING.md               # Contribution policy
├── ROADMAP.md                    # Future directions
└── AGENTS.md                     # This file
```

### 2. Architecture Rules (CRITICAL — Violations Will Be Rejected)

#### Creature vs Terrarium vs Root Agent

- **Creature**: Self-contained agent. Has its own LLM, tools, sub-agents, memory,
  I/O. Does **NOT** know it is in a terrarium. Sub-agents are VERTICAL hierarchy.
- **Terrarium**: Pure wiring layer. **No LLM, no intelligence, no decisions.**
  Loads creatures, creates channels, manages lifecycle.
- **Root Agent**: A creature that sits **OUTSIDE** the terrarium. Has terrarium
  management tools. **Never** a peer of creatures inside.

#### Two Composition Levels (NEVER MIX)

- **VERTICAL** (inside creature): controller -> sub-agents (private, hierarchical)
- **HORIZONTAL** (terrarium): creature <-> creature via channels (peer, opaque)

#### Controller Is an Orchestrator

- Controller outputs should be SHORT: tool calls, sub-agent dispatches, status updates
- Long user-facing content comes from **output sub-agents**
- This keeps controller lightweight, fast, and focused on decision-making

#### Tool Execution Is Async, Non-Blocking, Parallel

- Start tools the moment `##tool##` is detected during streaming via `asyncio.create_task()`
- **NEVER** queue tools until LLM finishes
- **NEVER** execute tools sequentially — run in parallel with `gather()`
- **NEVER** block LLM output for tool execution

### 3. Code Conventions

#### Python Style

- **Python 3.10+** minimum (CI matrix: 3.10–3.14)
- Modern type hints: `list`, `tuple`, `dict`, `X | None` — **NEVER** `List`, `Tuple`,
  `Dict`, `Optional`, `Union` from typing
- Prefer `match-case` over deeply nested `if-elif-else`
- Full asyncio throughout (mark sync modules as "require blocking" or "can be to_thread")

#### Import Rules

1. **No imports inside functions** (except optional deps and lazy imports to avoid long init time)
2. Import grouping order: built-in modules → third-party packages → `kohakuterrarium.*` modules
3. Within each group: `import` before `from`, shorter paths first, alphabetical

#### Logging

- **NEVER use `print()` in library code** — use structured logging
- Custom logger based on `logging` module (NOT loguru)
- Format: `[HH:MM:SS] [module.name] [LEVEL] message`
- **Avoid reserved LogRecord attributes** in extra kwargs: `name`, `msg`, `args`,
  `levelname`, `levelno`, `pathname`, `filename`, `module`, `lineno`, `funcName`,
  `created`, `msecs`, `relativeCreated`, `thread`, `threadName`, `process`,
  `processName`, `message`

#### File Limits

- Max 600 lines per file (hard max 1000, enforced by `tests/unit/test_file_sizes.py`)
- Highly modularized — one responsibility per module

#### Frontend

- Vue 3 + Vite, JavaScript only (no TypeScript)
- Run `npm run format:check` and `npm run build` before committing

### 4. Development Setup

```bash
git clone --recurse-submodules https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"

# Frontend (if touching the web UI)
cd src/kohakuterrarium-frontend && npm install
```

**Never** use `sys.path.insert` hacks in examples or tests. Always import from
the installed package (`kohakuterrarium.*`).

### 5. Pre-Flight Checks (Before Every Commit)

```bash
# Python lint and format
black src/ tests/
ruff check src/ tests/

# Unit tests
pytest tests/unit/ -q --ignore=tests/unit/test_file_sizes.py
pytest tests/unit/test_file_sizes.py -q

# Frontend (if applicable)
cd src/kohakuterrarium-frontend
npm ci
npm run format:check
npm run build
```

### 6. Post-Implementation Checklist

1. Verify all rules (ESPECIALLY no in-function imports, no `print()`, import order)
2. Run `black src/ tests/` and `ruff check src/ tests/`
3. Ensure new code has corresponding tests under `tests/unit/`
4. Logically separated git commits (one concept per commit)
5. For feature PRs: linked issue/discussion with maintainer approval **before** the PR

### 7. Adding New Things

#### Adding a Built-in Tool

1. Read `src/kohakuterrarium/builtins/tools/README.md`
2. Copy the pattern from a small existing tool (e.g., `glob.py`, `grep.py`)
3. Register in `builtins/tool_catalog.py`
4. Add matching skill doc under `builtin_skills/tools/<name>.md`
5. Add tests under `tests/unit/`

#### Adding a Built-in Sub-agent

1. Read `src/kohakuterrarium/builtins/subagents/README.md`
2. Use an existing config (e.g., `explore.py`, `research.py`) as template
3. Register in `builtins/subagent_catalog.py`
4. Add matching skill doc under `builtin_skills/subagents/<name>.md`
5. Add tests

#### Adding an LLM Preset

See `src/kohakuterrarium/llm/presets.py` for the dict shape, or use
`kt config llm add` interactively.

#### Adding a Plugin

Subclass `BasePlugin` from `modules/plugin/base.py`. Implement the hooks you
need. Register via config or via a package manifest. See
`examples/plugins/` for working examples.

#### Adding an Example or Package

Copy an existing folder under `examples/` and adapt. Packages need a
`kohaku.yaml` manifest — see `docs/en/guides/packages.md`.

### 8. Testing

- **Unit tests**: `tests/unit/` — fast, no network, use `ScriptedLLM` for
  deterministic LLM mock
- **Integration tests**: `tests/integration/` — may need API keys or network
- **Test harness**: `testing/llm.py` (ScriptedLLM), `testing/output.py`
  (OutputRecorder), `testing/events.py` (EventRecorder), `testing/agent.py`
  (TestAgentBuilder)
- **File-size guards**: `tests/unit/test_file_sizes.py` enforces 600-line limit
- Run `pytest tests/unit/ -q` locally; CI runs full matrix across Python
  3.10–3.14 × Linux/Windows/macOS

### 9. CI Matrix

Defined in `.github/workflows/ci.yml`. PRs are not reviewed until CI is green
on the contributor's fork:

1. **Lint**: `ruff check` + `black --check` (Python 3.13)
2. **Tests**: `pytest tests/unit/` on Python 3.10–3.14 × Linux/Windows/macOS
3. **File-size guards**: `pytest tests/unit/test_file_sizes.py`
4. **Frontend**: `npm ci` + `npm run format:check` + `npm run build`
5. **Wheel build**: build, install in clean venv, verify `kt --help`

**You must enable GitHub Actions on your fork** (Settings → Actions → Allow all
actions) and wait for green CI before opening a PR.

### 10. Key Design Principles

1. **Controller as orchestrator** — dispatches, never does heavy work itself
2. **Non-blocking everything** — tools, sub-agents, compaction all async
3. **Prompt auto-aggregation** — tool list and syntax are auto-generated, never
   manually maintained in system.md
4. **On-demand documentation** — `##info##` loads full docs only when needed
5. **Config-first** — creatures are defined by config + optional custom modules
6. **Two composition levels** — vertical (sub-agents) and horizontal (terrarium)
   are never mixed
7. **Append-only sessions** — event log is canonical history; conversation
   snapshots are a fast-resume optimization
8. **Module protocol** — every extensible surface (input, output, tool, trigger,
   sub-agent, plugin, user_command) follows a defined protocol in `modules/`

### 11. Contribution Rules

- **English only** for code, comments, commits, issues, PRs
- **Feature PRs require prior maintainer approval** (issue or Discord/QQ discussion)
- Bug fixes, docs, tests, and small refactors can go straight to PR (but
  still recommended to discuss first)
- Follow the PR template completely
- CI must be green on your fork before opening a PR
- See `CONTRIBUTING.md` for the full policy

### 12. Key Documentation Paths

| Topic | Path |
|-------|------|
| Architecture rules | `CLAUDE.md` |
| Contribution policy | `CONTRIBUTING.md` |
| Getting started | `docs/en/guides/getting-started.md` |
| First creature tutorial | `docs/en/tutorials/first-creature.md` |
| First terrarium tutorial | `docs/en/tutorials/first-terrarium.md` |
| First custom tool tutorial | `docs/en/tutorials/first-custom-tool.md` |
| First plugin tutorial | `docs/en/tutorials/first-plugin.md` |
| Creature authoring | `docs/en/guides/creatures.md` |
| Terrariums guide | `docs/en/guides/terrariums.md` |
| Configuration reference | `docs/en/reference/configuration.md` |
| CLI reference | `docs/en/reference/cli.md` |
| Built-ins reference | `docs/en/reference/builtins.md` |
| Plugin hooks reference | `docs/en/reference/plugin-hooks.md` |
| HTTP API reference | `docs/en/reference/http.md` |
| Python API reference | `docs/en/reference/python.md` |
| Internals / architecture | `docs/en/dev/internals.md` |
| Testing guide | `docs/en/dev/testing.md` |
| Frontend dev | `docs/en/dev/frontend.md` |
| Roadmap | `ROADMAP.md` |

### 13. Glossary

| Term | Meaning |
|------|---------|
| **Creature** | Self-contained agent with its own LLM, tools, sub-agents, triggers, memory, I/O |
| **Terrarium** | Pure wiring layer composing multiple creatures via channels |
| **Root Agent** | Creature outside the terrarium that manages it via tools |
| **Controller** | The main LLM reasoning loop inside a creature |
| **Sub-agent** | Nested agent with own controller + tools, delegated by the controller |
| **Channel** | Communication substrate (queue or broadcast) between creatures |
| **Trigger** | Automatic event source (timer, scheduler, channel message, custom) |
| **Plugin** | Hook-based extension that modifies connections between modules |
| **Skill** | Procedural knowledge loaded on-demand (markdown manifest) |
| **Tool** | Executable function the LLM can call |
| **Package** | Shareable bundle of creatures, tools, plugins, etc. with `kohaku.yaml` manifest |
| **Session** | Persisted `.kohakutr` file capturing operational state |
| **Scratchpad** | Session-scoped key-value store shared across agents |
| **Output wiring** | Config-driven deterministic delivery of creature output to targets |
| **Compose algebra** | `>>` (sequence), `&` (parallel), `\|` (fallback), `*` (retry) operators |
| **MCP** | Model Context Protocol — client-server tool exposure over stdio/HTTP |
| **kt-biome** | Official showcase pack of OOTB creatures, terrariums, and plugins |
