<p align="center">
  <img src="images/banner.png" alt="KohakuTerrarium" width="800">
</p>
<p align="center">
  <strong>The machine for building agents — so you stop rebuilding the machine every time you want a new one.</strong>
</p>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-KohakuTerrarium--1.0-green" alt="License">
  <img src="https://img.shields.io/badge/version-1.0.0-orange" alt="Version">
</p>

<p align="center">
  <strong>English</strong> &nbsp;·&nbsp; <a href="README.zh.md">繁體中文</a>
</p>

---

## See it run (60 seconds)

```bash
pip install kohakuterrarium                                         # install
kt login codex                                                      # authenticate
kt install https://github.com/Kohaku-Lab/kt-biome.git            # get OOTB creatures
kt run @kt-biome/creatures/swe --mode cli                        # run one
```

You get an interactive shell with a full coding agent — file tools, shell access, web search, sub-agents, resumable sessions. `Ctrl+D` exits; `kt resume --last` picks back up.

Want more hand-holding? [Getting Started](docs/en/guides/getting-started.md). Want to build your own? [First Creature](docs/en/tutorials/first-creature.md).

## Is this for you?

**You probably want KohakuTerrarium if** you need a new agent shape and don't want to rebuild the substrate; you want OOTB creatures you can customise; you want to embed agent behaviour in existing Python; your requirements are still evolving.

**You probably don't if** an existing agent product (Claude Code, Codex, …) already fits your stable needs; your mental model doesn't map onto controller / tools / triggers / sub-agents / channels; you need sub-50 ms per-operation latency. More honesty at [boundaries](docs/en/concepts/boundaries.md).

## What KohakuTerrarium is

KohakuTerrarium is a framework for building agents — not another agent.

The last two years produced a striking number of agent products: Claude Code, Codex, OpenClaw, Gemini CLI, Hermes Agent, OpenCode, and many more. They are genuinely different, and they all re-implement the same substrate from scratch: a controller loop, tool dispatch, trigger system, sub-agent mechanism, sessions, persistence, multi-agent wiring. Every new agent shape costs a new ground-up reimplementation of the plumbing.

KohakuTerrarium's job is to put that substrate in one place so the next agent shape costs a config file and a few custom modules, not a new repo.

The core abstraction is the **creature**: a standalone agent with its own controller, tools, sub-agents, triggers, memory, and I/O. Creatures compose horizontally into a **terrarium** — a pure wiring layer. Everything is Python, so agents can be embedded inside tools, triggers, plugins, and outputs of other agents.

For out-of-the-box creatures you can try today, see [**kt-biome**](https://github.com/Kohaku-Lab/kt-biome) — the showcase pack of useful agents and plugins built on top of the framework.

## Where it fits

|  | Product | Framework | Utility / Wrapper |
|--|---------|-----------|-------------------|
| **LLM App** | ChatGPT, Claude.ai | LangChain, LangGraph, Dify | DSPy |
| **Agent** | ***kt-biome***, Claude Code, Codex, OpenCode, OpenClaw, Hermes Agent… | ***KohakuTerrarium***, smolagents | — |
| **Multi-Agent** | ***kt-biome*** | ***KohakuTerrarium***  | CrewAI, AutoGen |

Most tooling sits below the agent layer or jumps straight to multi-agent orchestration with a thin idea of what an agent is. KohakuTerrarium starts with the agent itself.

A creature is made of:

- **Controller** — the reasoning loop
- **Input** — how events enter the agent
- **Output** — how results leave the agent
- **Tools** — what actions it can take
- **Triggers** — what wakes it up
- **Sub-agents** — internal delegation for specialised tasks

A terrarium composes multiple creatures horizontally through channels, lifecycle management, and observability.

## Key features

- **Agent-level abstraction.** The six-module creature model is the first-class concept. Every new agent shape is "write a config and maybe a few custom modules," not "rebuild the runtime."
- **Built-in session persistence and resume.** Sessions store operational state, not just chat history. Resume a run hours later with `kt resume`.
- **Searchable session history.** Every event is indexed. `kt search` and the `search_memory` tool let you (and the agent) look up past work.
- **Non-blocking context compaction.** Long-running agents keep working while context is compacted in the background.
- **Comprehensive built-in tools and sub-agents.** File, shell, web, JSON, search, editing, planning, review, research, terrarium management.
- **MCP support.** Connect stdio / HTTP MCP servers per-agent or globally; tools surface in the prompt automatically.
- **Package system.** Install creatures / terrariums / plugins / LLM presets from Git or local paths; compose installed packages with inheritance.
- **Python-native.** Agents are async Python objects. Embed them inside tools, triggers, plugins, or outputs of other agents.
- **Composition algebra.** `>>`, `&`, `|`, `*`, `.iterate` operators for stitching agents into pipelines programmatically.
- **Multiple runtime surfaces.** CLI, TUI, web dashboard, and desktop app out of the box.
- **Useful OOTB creatures via [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome).** Start by running strong default agents; customise or inherit from them later.

## Quick start

### 1. Install KohakuTerrarium

```bash
# From PyPI
pip install kohakuterrarium
# Optional extras: pip install "kohakuterrarium[full]"

# Or from source (for development — uv is the project convention)
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"

# Build the web frontend (required for `kt web` / `kt app` from source)
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

### 2. Install OOTB creatures and plugins

```bash
# Official showcase pack
kt install https://github.com/Kohaku-Lab/kt-biome.git

# Any third-party package
kt install <git-url>
kt install ./my-creatures -e        # editable install
```

### 3. Authenticate a model provider

```bash
# Codex OAuth (ChatGPT subscription)
kt login codex
kt model default gpt-5.4

# Or any OpenAI-compatible provider via `kt config provider add`
```

Supports OpenRouter, OpenAI, Anthropic, Google Gemini, and any OpenAI-compatible API.

### 4. Run something

```bash
# Single creature
kt run @kt-biome/creatures/swe --mode cli
kt run @kt-biome/creatures/researcher

# Multi-agent terrarium
kt terrarium run @kt-biome/terrariums/swe_team

# Web dashboard
kt serve start

# Native desktop
kt app
```

## Choose your path

### I want to run something now

- [Getting Started](docs/en/guides/getting-started.md)
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome)
- [CLI Reference](docs/en/reference/cli.md)
- [Examples](examples/README.md)

### I want to build my own creature

- [First Creature tutorial](docs/en/tutorials/first-creature.md)
- [Creatures guide](docs/en/guides/creatures.md)
- [Custom Modules](docs/en/guides/custom-modules.md)
- [Plugins](docs/en/guides/plugins.md)
- [First Custom Tool tutorial](docs/en/tutorials/first-custom-tool.md)

### I want multi-agent composition

- [First Terrarium tutorial](docs/en/tutorials/first-terrarium.md)
- [Terrariums guide](docs/en/guides/terrariums.md)
- [Multi-agent concept](docs/en/concepts/multi-agent/README.md)

### I want to embed it in Python

- [First Python Embedding tutorial](docs/en/tutorials/first-python-embedding.md)
- [Programmatic Usage](docs/en/guides/programmatic-usage.md)
- [Composition Algebra](docs/en/guides/composition.md)
- [Python API](docs/en/reference/python.md)

### I want to understand what's going on

- [Concept docs](docs/en/concepts/README.md)
- [Glossary](docs/en/concepts/glossary.md) — plain-English definitions
- [Why KohakuTerrarium](docs/en/concepts/foundations/why-kohakuterrarium.md)
- [What is an agent](docs/en/concepts/foundations/what-is-an-agent.md)

### I want to work on the framework itself

- [Development home](docs/en/dev/README.md)
- [Internals](docs/en/dev/internals.md)
- [Testing](docs/en/dev/testing.md)
- Package READMEs under [`src/kohakuterrarium/`](src/kohakuterrarium/README.md)

## Core mental model

### Creature

```text
    List, Create, Delete  +------------------+
                    +-----|   Tools System   |
      +---------+   |     +------------------+
      |  Input  |   |          ^        |
      +---------+   V          |        v
        |   +---------+   +------------------+   +--------+
        +-->| Trigger |-->|    Controller    |-->| Output |
User input  | System  |   |    (Main LLM)    |   +--------+
            +---------+   +------------------+
                              |          ^
                              v          |
                          +------------------+
                          |    Sub Agents    |
                          +------------------+
```

A creature is a standalone agent with its own runtime, tools, sub-agents, prompts, and state.

```bash
kt run path/to/creature
kt run @package/path/to/creature
```

### Terrarium

```text
  +---------+       +---------------------------+
  |  User   |<----->|        Root Agent         |
  +---------+       |  (terrarium tools, TUI)   |
                    +---------------------------+
                          |               ^
            sends tasks   |               |  observes results
                          v               |
                    +---------------------------+
                    |     Terrarium Layer       |
                    |   (pure wiring, no LLM)   |
                    +-------+----------+--------+
                    |  swe  |  coder   |  ....  |
                    +-------+----------+--------+
```

A terrarium is a pure wiring layer that manages creature lifecycles, the channels between them, and the framework-level **output wiring** that auto-delivers a creature's turn-end output to named targets. No LLM, no decisions — just runtime. Creatures don't know they're in a terrarium; they run standalone too.

Terrarium is our **proposed architecture** for horizontal multi-agent — two complementary cooperation mechanisms (channels for conditional / optional traffic; output wiring for deterministic pipeline edges), plus hot-plug and observation. Still evolving as patterns emerge; the [ROADMAP](ROADMAP.md) has the open questions. Prefer sub-agents (vertical) when a single creature can decompose the task itself — it's the simpler answer for most "I need context isolation" instincts.

### Root agent

A terrarium can define a root agent that sits outside the team and operates it through terrarium management tools. The user talks to root; root talks to the team.

### Channels

Channels are the communication substrate — for terrariums and for agent-to-agent patterns inside a single creature.

- **Queue** — one consumer receives each message
- **Broadcast** — all subscribers receive each message

### Modules

A creature has six conceptual modules. **Five of them are user-extensible** — you swap their implementations in config or in Python. The sixth, the controller, is the reasoning loop that drives them; you rarely swap it (and when you do, you're writing the framework's successor).

| Module | What it does | Example custom use |
|--------|---------------|--------------------|
| **Input** | Receives external events | Discord listener, webhook, voice input |
| **Output** | Delivers agent output | Discord sender, TTS, file writer |
| **Tool** | Executes actions | API calls, database access, RAG retrieval |
| **Trigger** | Generates automatic events | Timer, scheduler, channel watcher |
| **Sub-agent** | Delegated task execution | Planning, code review, research |

Plus **plugins**, which modify the connections *between* modules without forking them (prompt plugins, lifecycle hooks). See [plugins guide](docs/en/guides/plugins.md).

### Environment and session

- **Environment** — shared terrarium state (shared channels).
- **Session** — private creature state (scratchpad, private channels, sub-agent state).

Private by default, shared by opt-in.

## Practical capabilities

KohakuTerrarium already ships:

- Built-in file, shell, web, JSON, channel, trigger, and introspection tools, including single-edit and multi-edit file mutation primitives.
- Built-in sub-agents for exploration, planning, implementation, review, summarisation, and research.
- Background tool execution and non-blocking agent flow.
- Session persistence with resumable operational state.
- FTS + vector memory search (model2vec / sentence-transformer / API embedding providers).
- Non-blocking auto-compaction for long-running agents.
- MCP (Model Context Protocol) integration — stdio and HTTP transports.
- Package manager for creatures, plugins, terrariums, and reusable agent packs (`kt install`, `kt update`).
- Python embedding through `Agent`, `AgentSession`, `TerrariumRuntime`, and `KohakuManager`.
- HTTP and WebSocket serving.
- Web dashboard and native desktop app.
- Custom module and plugin systems.

## Programmatic usage

Agents are async Python values. Embed them:

```python
import asyncio
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime

async def main():
    # Single agent
    agent = Agent.from_path("@kt-biome/creatures/swe")
    agent.set_output_handler(lambda text: print(text, end=""), replace_default=True)
    await agent.start()
    await agent.inject_input("Explain what this codebase does.")
    await agent.stop()

    # Multi-agent terrarium
    runtime = TerrariumRuntime(load_terrarium_config("@kt-biome/terrariums/swe_team"))
    await runtime.start()
    tasks = runtime.environment.shared_channels.get("tasks")
    await tasks.send(ChannelMessage(sender="user", content="Fix the auth bug."))
    await runtime.run()
    await runtime.stop()

asyncio.run(main())
```

### Composition algebra

Because agents are Python values, they compose with operators. `>>` (sequence), `&` (parallel), `|` (fallback), `*N` (retry), `.iterate` (async loop):

```python
import asyncio
from kohakuterrarium.compose import agent, factory
from kohakuterrarium.core.config import load_agent_config

def make_agent(name, prompt):
    config = load_agent_config("@kt-biome/creatures/general")
    config.name, config.system_prompt, config.tools, config.subagents = name, prompt, [], []
    return config

async def main():
    # Persistent agents (accumulate conversation)
    async with await agent(make_agent("writer", "You are a writer.")) as writer, \
               await agent(make_agent("reviewer", "You are a strict reviewer. Say APPROVED if good.")) as reviewer:

        pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer

        async for feedback in pipeline.iterate("Write a haiku about coding"):
            print(f"Reviewer: {feedback[:100]}")
            if "APPROVED" in feedback:
                break

    # Parallel ensemble with retry + fallback
    fast = factory(make_agent("fast", "Answer concisely."))
    deep = factory(make_agent("deep", "Answer thoroughly."))
    safe = (fast & deep) >> (lambda results: max(results, key=len))
    safe_with_retry = (safe * 2) | fast
    print(await safe_with_retry("What is recursion?"))

asyncio.run(main())
```

More: [Programmatic Usage](docs/en/guides/programmatic-usage.md), [Composition](docs/en/guides/composition.md), [Python API](docs/en/reference/python.md), and [`examples/code/`](examples/).

## Runtime surfaces

### CLI and TUI

- **cli** — rich inline terminal experience
- **tui** — full-screen Textual application
- **plain** — simple stdout/stdin for pipes and CI

See [CLI Reference](docs/en/reference/cli.md).

### Web dashboard

Vue-based dashboard + FastAPI server.

```bash
kt web                       # one-shot, foreground
kt serve start               # long-running daemon
# Frontend dev: npm run dev --prefix src/kohakuterrarium-frontend
```

See [HTTP API](docs/en/reference/http.md), [Serving guide](docs/en/guides/serving.md), [Frontend Architecture](docs/en/dev/frontend.md).

### Desktop app

`kt app` launches the web UI inside a native desktop window (requires `pywebview`).

## Sessions, memory, and resume

Sessions save to `~/.kohakuterrarium/sessions/` unless disabled.

```bash
kt resume            # pick interactively
kt resume --last     # resume most recent
kt resume swe_team   # resume by name prefix
```

The same store powers searchable history:

```bash
kt embedding <session>                       # build FTS + vector indices
kt search <session> "auth bug fix"           # hybrid/semantic/FTS search
```

And the agent can search its own history via the `search_memory` tool.

`.kohakutr` files store conversation, tool calls, events, scratchpad, sub-agent state, channel messages, jobs, resumable triggers, and config metadata.

See [Sessions](docs/en/guides/sessions.md), [Memory](docs/en/guides/memory.md).

## Packages, defaults, and examples

Creatures are meant to be packaged, installed, reused, and shared.

```bash
kt install https://github.com/someone/cool-creatures.git
kt install ./my-creatures -e
kt list
kt update --all
```

Run installed configs with package references:

```bash
kt run @cool-creatures/creatures/my-agent
kt terrarium run @cool-creatures/terrariums/my-team
```

Available resources:

- [`kt-biome/`](https://github.com/Kohaku-Lab/kt-biome) — official showcase creatures, terrariums, and plugin pack
- `examples/agent-apps/` — config-driven creature examples
- `examples/code/` — Python usage examples
- `examples/terrariums/` — multi-agent examples
- `examples/plugins/` — plugin examples

See [examples/README.md](examples/README.md).

## Codebase map

```text
src/kohakuterrarium/
  core/              # Agent runtime, controller, executor, events, environment
  bootstrap/         # Agent initialisation factories (LLM, tools, I/O, triggers, plugins)
  cli/               # `kt` command dispatcher
  terrarium/         # Multi-agent runtime, topology wiring, hot-plug, persistence
  builtins/          # Built-in tools, sub-agents, I/O modules, TUI, user commands, CLI UI
  builtin_skills/    # Markdown skill manifests for on-demand docs
  session/           # Session persistence, memory search, embeddings
  serving/           # Transport-agnostic service manager and event streaming
  api/               # FastAPI HTTP + WebSocket server
  compose/           # Composition algebra primitives
  mcp/               # MCP client manager
  modules/           # Base protocols for tools, inputs, outputs, triggers, sub-agents, user commands
  llm/               # LLM providers, profiles, API key management
  parsing/           # Tool-call parsing and stream handling
  prompt/            # Prompt aggregation, plugins, skill loading
  testing/           # Test infrastructure (ScriptedLLM, TestAgentBuilder, recorders)

src/kohakuterrarium-frontend/   # Vue web frontend
kt-biome/                    # (separate repo) Official OOTB pack
examples/                       # Example creatures, terrariums, code samples, plugins
docs/                           # Tutorials, guides, concepts, reference, dev
```

Every subpackage has its own README describing files, dependency direction, and invariants.

## Documentation map

Full docs live in [`docs/`](docs/en/README.md).

### Tutorials
[First Creature](docs/en/tutorials/first-creature.md) · [First Terrarium](docs/en/tutorials/first-terrarium.md) · [First Python Embedding](docs/en/tutorials/first-python-embedding.md) · [First Custom Tool](docs/en/tutorials/first-custom-tool.md) · [First Plugin](docs/en/tutorials/first-plugin.md)

### Guides
[Getting Started](docs/en/guides/getting-started.md) · [Creatures](docs/en/guides/creatures.md) · [Terrariums](docs/en/guides/terrariums.md) · [Sessions](docs/en/guides/sessions.md) · [Memory](docs/en/guides/memory.md) · [Configuration](docs/en/guides/configuration.md) · [Programmatic Usage](docs/en/guides/programmatic-usage.md) · [Composition](docs/en/guides/composition.md) · [Custom Modules](docs/en/guides/custom-modules.md) · [Plugins](docs/en/guides/plugins.md) · [MCP](docs/en/guides/mcp.md) · [Packages](docs/en/guides/packages.md) · [Serving](docs/en/guides/serving.md) · [Examples](docs/en/guides/examples.md)

### Concepts
[Glossary](docs/en/concepts/glossary.md) · [Why KohakuTerrarium](docs/en/concepts/foundations/why-kohakuterrarium.md) · [What is an agent](docs/en/concepts/foundations/what-is-an-agent.md) · [Composing an agent](docs/en/concepts/foundations/composing-an-agent.md) · [Modules](docs/en/concepts/modules/README.md) · [Agent as a Python object](docs/en/concepts/python-native/agent-as-python-object.md) · [Composition algebra](docs/en/concepts/python-native/composition-algebra.md) · [Multi-agent](docs/en/concepts/multi-agent/README.md) · [Patterns](docs/en/concepts/patterns.md) · [Boundaries](docs/en/concepts/boundaries.md)

### Reference
[CLI](docs/en/reference/cli.md) · [HTTP](docs/en/reference/http.md) · [Python API](docs/en/reference/python.md) · [Configuration](docs/en/reference/configuration.md) · [Builtins](docs/en/reference/builtins.md) · [Plugin hooks](docs/en/reference/plugin-hooks.md)

## Roadmap

Near-term directions include more reliable terrarium flow, richer UI output / interaction modules across CLI / TUI / web, more built-in creatures, plugins, and integrations, and better daemon-backed workflows for long-running and remote usage. See [ROADMAP.md](ROADMAP.md).

## Contributing

- [Contributing docs](docs/en/dev/README.md)
- [Testing](docs/en/dev/testing.md)
- [Internals](docs/en/dev/internals.md)
- [Frontend architecture](docs/en/dev/frontend.md)

## License

[KohakuTerrarium License 1.0](LICENSE): based on Apache-2.0 with naming and attribution requirements.

- Derivative works must include `Kohaku` or `Terrarium` in their name.
- Derivative works must provide visible attribution with a link to this project.

Copyright 2024-2026 Shih-Ying Yeh (KohakuBlueLeaf) and contributors.
