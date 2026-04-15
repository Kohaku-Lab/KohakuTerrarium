<p align="center">
  <img src="images/banner.png" alt="KohakuTerrarium" width="800">
</p>
<p align="center">
  <strong>Define what an agent is. Build any kind. Use useful ones out of the box.</strong>
</p>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-KohakuTerrarium--1.0-green" alt="License">
  <img src="https://img.shields.io/badge/version-1.0.0rc3-orange" alt="Version">
</p>

---

## What KohakuTerrarium is

KohakuTerrarium is a framework for building real agents, not just LLM wrappers.

Its core abstraction is the **creature**: a standalone agent with its own controller, tools, sub-agents, triggers, memory, and I/O. Creatures can run by themselves, or they can be composed into a **terrarium**, which is a pure multi-agent wiring layer.

The goal is simple: make agent systems modular enough to model serious products, while still staying configurable, composable, and hackable.

With [**kt-defaults**](https://github.com/Kohaku-Lab/kt-defaults), its official OOTB creature pack and plugin set, you can use KohakuTerrarium directly as a powerful coding agent runtime through the CLI, TUI, web app, or desktop app, without needing to understand the whole framework first.

## Where it fits

AI tooling usually lives at different layers:

|  | Product | Framework | Utility / Wrapper |
|--|---------|-----------|-------------------|
| **LLM App** | ChatGPT, Claude.ai | LangChain, LangGraph, Dify | DSPy |
| **Agent** | ***kt-defaults***, Claude Code, Codex, OpenCode, OpenClaw, Hermes Agent... | ***KohakuTerrarium***, smolagents | - |
| **Multi-Agent** | ***kt-defaults*** | ***KohakuTerrarium*** | CrewAI, AutoGen |

Most frameworks either operate below the agent layer, or they jump straight to multi-agent orchestration with a very thin idea of what an agent is.

KohakuTerrarium starts with the agent itself.

A creature is made of:

- **Controller**: the reasoning loop
- **Input**: how events enter the agent
- **Output**: how results leave the agent
- **Tools**: what actions it can take
- **Triggers**: what wakes it up
- **Sub-agents**: internal delegation for specialized tasks

Then a terrarium composes multiple creatures horizontally through channels, lifecycle management, and observability.

KohakuTerrarium is not only something you build from scratch. It also supports an installable ecosystem of reusable creatures, terrariums, plugins, and other modules. The official package for that is [`kt-defaults/`](kt-defaults/README.md), which provides useful out-of-the-box creatures plus a practical plugin pack.

## Key features

- **Modular agent architecture**
  - The core framework feature.
  - Build your own creature by writing config, prompts, and only the custom modules you actually need.
  - You do not need to rebuild the whole agent runtime from scratch.
- **Built-in session persistence and resume**
  - Sessions store operational state, not just chat history.
- **Built-in scratchpad and persistent session history**
  - Session history is stored not only for resume, but also as a searchable knowledge base.
  - Past runs can be searched in FTS or vector form, including by agents through built-in memory search tools.
- **Built-in non-blocking auto-compaction**
  - Long-running agents can keep operating while context is compacted in the background.
- **Comprehensive built-in tools and sub-agents**
  - File, shell, web, JSON, search, editing, planning, review, research, and more.
- **Multiple built-in runtime surfaces**
  - CLI, TUI, web, and desktop app support out of the box.
- **Useful OOTB creatures through `kt-defaults`**
  - You can start by using strong default agents, then customize or inherit from them later.
- **Strong programmatic usage path**
  - Use agents directly from Python.
  - Compose them with the composition algebra when your application is the orchestrator.

## Quick start

### 1. Install KohakuTerrarium

```bash
# Install from PyPI
pip install kohakuterrarium
# Or: pip install "kohakuterrarium[full]" for more optional dependencies

# Or install from source (for development)
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
pip install -e ".[dev]"

# Build the web frontend for `kt web` / `kt app`
# Required when running from source
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

### 2. Install OOTB creatures and plugins

```bash
# Install the official OOTB creature and plugin pack
kt install https://github.com/Kohaku-Lab/kt-defaults.git

# You can also install shared third-party packages
kt install <git-url>
```

### 3. Authenticate a model provider

```bash
# Codex OAuth (no API key needed)
kt login codex
kt model default gpt-5.4

# Or configure another OpenAI-compatible provider
```

### 4. Run something useful

```bash
# Run a single creature
kt run @kt-defaults/creatures/swe --mode cli
kt run @kt-defaults/creatures/reviewer

# Optional: run a multi-agent terrarium
kt terrarium run @kt-defaults/terrariums/swe_team

# Launch the web server
kt serve start

# Launch the desktop app
kt app
```

Supports OpenRouter, OpenAI, Anthropic, Google Gemini, and any OpenAI-compatible API.

## Choose your path

### I want to run something now

- Start with [Getting Started](docs/guides/getting-started.md)
- Browse [`kt-defaults/`](kt-defaults/README.md)
- Browse [CLI Reference](docs/reference/cli.md)
- See included [examples](examples/README.md)

### I want to build my own creature

- Read [Creatures](docs/guides/creatures.md)
- Read [Configuration Reference](docs/guides/configuration.md)
- See [Custom Modules](docs/guides/custom-modules.md)
- See [Plugins](docs/guides/plugins.md)

### I want optional multi-agent composition

- Read [Terrariums](docs/guides/terrariums.md)
- Read [Channels](docs/concepts/channels.md)
- Read [Agents](docs/concepts/agents.md)

### I want to embed it in Python

- Read [Programmatic Usage](docs/guides/programmatic-usage.md)
- Read [Python API](docs/reference/python.md)
- See `examples/code/`

### I want to work on the framework itself

- Start with [docs/](docs/README.md)
- Read [Testing](docs/dev/testing.md)
- Read [Framework Internals](docs/dev/internals.md)
- Read package READMEs in `src/kohakuterrarium/`

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

The creature is the first-class abstraction in KohakuTerrarium.

You can run it directly:

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
                    |  swe  | reviewer |  ....  |
                    +-------+----------+--------+
```

A terrarium is a composition layer that wires creatures together through channels and manages their lifecycle.

### Root agent

A terrarium can define a root agent that sits outside the team and operates it through terrarium management tools.

### Channels

Channels are how creatures communicate:

- **Queue**: one consumer receives each message
- **Broadcast**: all subscribers receive each message

### Modules

Everything extensible in KohakuTerrarium fits into one of five main module types:

| Module | What it does | Example custom use |
|--------|---------------|--------------------|
| **Input** | Receives external events | Discord listener, webhook, voice input |
| **Output** | Delivers agent output | Discord sender, TTS, file writer |
| **Tool** | Executes actions | API calls, database access, RAG retrieval |
| **Trigger** | Generates automatic events | Timer, scheduler, channel watcher |
| **Sub-agent** | Delegated task execution | Planning, code review, research |

### Session and environment

- **Environment**: shared terrarium state such as shared channels
- **Session**: private creature state such as scratchpad and sub-agent state

That keeps team communication shared while creature internals stay isolated.

## Practical capabilities

KohakuTerrarium already includes a broad runtime surface:

- built-in file, shell, web, JSON, channel, trigger, and introspection tools, including single-edit and multi-edit file mutation primitives
- built-in sub-agents for exploration, planning, implementation, review, summarization, and research
- background tool execution and non-blocking agent flow
- session persistence with resumable operational state, not just chat history
- package installation for creatures, plugins, terrariums, and reusable agent packs
- Python embedding through `Agent`, `TerrariumRuntime`, and `KohakuManager`
- HTTP and WebSocket serving
- web dashboard and native desktop app
- custom module and plugin systems

## Programmatic usage

Use agents and terrariums as libraries, with your code as the orchestrator:

```python
import asyncio
from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.channel import ChannelMessage
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.runtime import TerrariumRuntime

async def main():
    # ── Single agent ──────────────────────────────────────────────
    agent = Agent.from_path("@kt-defaults/creatures/swe")
    agent.set_output_handler(lambda text: print(text, end=""), replace_default=True)
    await agent.start()
    await agent.inject_input("Explain what this codebase does.")
    await agent.stop()

    # ── Multi-agent terrarium ─────────────────────────────────────
    runtime = TerrariumRuntime(load_terrarium_config("@kt-defaults/terrariums/swe_team"))
    await runtime.start()
    tasks = runtime.environment.shared_channels.get("tasks")
    await tasks.send(ChannelMessage(sender="user", content="Fix the auth bug."))
    await runtime.run()
    await runtime.stop()

asyncio.run(main())
```

### Composition algebra

Compose agents with Python operators: `>>` (sequence), `&` (parallel), `|` (fallback), `*` (retry), `async for` (loop):

```python
import asyncio
from kohakuterrarium.compose import agent, factory
from kohakuterrarium.core.config import load_agent_config

def make_agent(name, prompt):
    config = load_agent_config("@kt-defaults/creatures/general")
    config.name, config.system_prompt, config.tools, config.subagents = name, prompt, [], []
    return config

async def main():
    # Persistent agents (accumulate conversation context)
    async with await agent(make_agent("writer", "You are a writer.")) as writer, \
               await agent(make_agent("reviewer", "You are a strict reviewer. Say APPROVED if good.")) as reviewer:

        # Pipeline: writer >> bridge >> reviewer
        pipeline = writer >> (lambda text: f"Review this:\n{text}") >> reviewer

        # Loop until approved (native Python control flow)
        async for feedback in pipeline.iterate("Write a haiku about coding"):
            print(f"Reviewer: {feedback[:100]}")
            if "APPROVED" in feedback:
                break

    # Parallel ensemble with fallback
    fast = factory(make_agent("fast", "Answer concisely."))
    deep = factory(make_agent("deep", "Answer thoroughly."))
    safe = (fast & deep) >> (lambda results: max(results, key=len))  # pick best
    safe_with_retry = (safe * 2) | fast  # retry twice, then fallback
    print(await safe_with_retry("What is recursion?"))

asyncio.run(main())
```

For more, see [Programmatic Usage](docs/guides/programmatic-usage.md), [Python API](docs/reference/python.md), and `examples/code/`.

## Runtime surfaces

### CLI and TUI

KohakuTerrarium supports multiple interactive modes:

- **cli**: rich inline terminal experience
- **tui**: full-screen Textual application
- **plain**: simple stdout and stdin mode for piping and CI

See [CLI Reference](docs/reference/cli.md) for command details.

### Web dashboard

The project includes a Vue-based dashboard and FastAPI server.

```bash
python -m kohakuterrarium.api.main
# For frontend development:
npm run dev --prefix src/kohakuterrarium-frontend
```

See [HTTP API](docs/reference/http.md) and [Frontend Architecture](docs/dev/frontend.md).

### Desktop app

`kt app` launches the same web UI inside a native desktop window.

## Sessions and persistence

Sessions are automatically saved to `~/.kohakuterrarium/sessions/` unless disabled.

Resume anytime:

```bash
kt resume
kt resume --last
kt resume swe_team
```

Session files use the `.kohakutr` format and store operational state such as:

That history is not only for resuming a past session. It also acts as a knowledge database that can be searched later, including through full-text search and vector search. Agents can use the built-in memory search tools to retrieve useful history from prior work, similar to querying a RAG-style store.

- conversation history
- tool call metadata
- event logs
- scratchpad state
- sub-agent state
- channel messages
- jobs
- resumable triggers
- config and topology metadata

See [Sessions](docs/guides/sessions.md).

## Packages, defaults, and examples

One of the important practical ideas in KohakuTerrarium is that creatures are meant to be packaged, installed, reused, and shared. You can use official defaults directly, inherit from them, or install someone else's creature/plugin package and run it as-is.

Install creature and terrarium packages from Git or local paths:

```bash
kt install https://github.com/someone/cool-creatures.git
kt install ./my-creatures -e
kt list
```

Run installed configs with package references:

```bash
kt run @cool-creatures/creatures/my-agent
kt terrarium run @cool-creatures/terrariums/my-team
```

Included resources:

- `kt-defaults/` contains official OOTB creatures, installable terrariums, and a useful plugin pack
- `examples/agent-apps/` contains config-driven creature examples
- `examples/code/` contains Python usage examples
- `examples/terrariums/` contains optional multi-agent examples
- `examples/plugins/` contains plugin examples

See [examples/README.md](examples/README.md) and [kt-defaults/README.md](kt-defaults/README.md).

## Codebase map

```text
src/kohakuterrarium/
  core/           # Agent runtime, controller, executor, events, environment
  bootstrap/      # Agent initialization factories for LLM, tools, I/O, triggers
  cli/            # CLI command handlers
  terrarium/      # Multi-agent runtime, config loading, topology wiring, hot-plug
  builtins/       # Built-in tools, sub-agents, I/O modules, TUI, user commands
  builtin_skills/ # Markdown skill manifests for on-demand tool and sub-agent docs
  session/        # Session persistence, memory search, embeddings
  serving/        # Transport-agnostic service manager and event streaming
  api/            # FastAPI HTTP and WebSocket server
  modules/        # Base protocols for tools, inputs, outputs, triggers, sub-agents
  llm/            # LLM providers, profiles, API key management
  parsing/        # Tool-call parsing and stream handling
  prompt/         # Prompt assembly, aggregation, plugins, skill loading
  testing/        # Test infrastructure

src/kohakuterrarium-frontend/  # Vue web frontend
kt-defaults/                   # Official OOTB creatures, terrariums, and plugin pack
examples/                      # Example creatures, terrariums, code samples, plugins
docs/                          # Guides, concepts, API reference, contributor docs
```

Several source packages also include local `README.md` files that explain internal responsibilities and dependency flow. Those are worth reading if you are contributing to the framework.

## Documentation map

Full documentation lives in [`docs/`](docs/README.md).

### Guides

- [Getting Started](docs/guides/getting-started.md)
- [Configuration Reference](docs/guides/configuration.md)
- [Creatures](docs/guides/creatures.md)
- [Terrariums](docs/guides/terrariums.md)
- [Sessions](docs/guides/sessions.md)
- [Programmatic Usage](docs/guides/programmatic-usage.md)
- [Custom Modules](docs/guides/custom-modules.md)
- [Plugins](docs/guides/plugins.md)
- [Examples](docs/guides/examples.md)

### Concepts

- [Overview](docs/concepts/overview.md)
- [Agents](docs/concepts/agents.md)
- [Terrariums](docs/concepts/terrariums.md)
- [Channels](docs/concepts/channels.md)
- [Execution Model](docs/concepts/execution.md)
- [Prompt System](docs/concepts/prompts.md)
- [Serving Layer](docs/concepts/serving.md)
- [Environment and Session](docs/concepts/environment.md)
- [Tool Formats](docs/concepts/tool-formats.md)

### API reference

- [Python API](docs/reference/python.md)
- [HTTP API](docs/reference/http.md)
- [CLI Reference](docs/reference/cli.md)

### Contributing

- [Contributing docs](docs/dev/README.md)
- [Testing](docs/dev/testing.md)
- [Framework Internals](docs/dev/internals.md)
- [Frontend Architecture](docs/dev/frontend.md)

## License

[KohakuTerrarium License 1.0](LICENSE): based on Apache-2.0 with naming and attribution requirements.

- Derivative works must include `Kohaku` or `Terrarium` in their name
- Derivative works must provide visible attribution with a link to this project

Copyright 2024-2026 Shih-Ying Yeh (KohakuBlueLeaf) and contributors
