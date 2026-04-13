<p align="center">
  <img src="images/banner.png" alt="KohakuTerrarium" width="800">
</p>
<p align="center">
  <strong>Define what an agent is. Build any kind. Compose them into teams.</strong>
</p>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-KohakuTerrarium--1.0-green" alt="License">
  <img src="https://img.shields.io/badge/version-1.0.0rc1-orange" alt="Version">
</p>

---

## What KohakuTerrarium is

KohakuTerrarium is a framework for building real agents, not just LLM wrappers.

Its core abstraction is the **creature**: a standalone agent with its own controller, tools, sub-agents, triggers, memory, and I/O. Creatures can run by themselves, or they can be composed into a **terrarium**, which is a pure multi-agent wiring layer.

The goal is simple: make agent systems modular enough to model serious products, while still staying configurable, composable, and hackable.

## Where it fits

AI tooling usually lives at different layers:

|  | Product | Framework | Utility / Wrapper |
|--|---------|-----------|-------------------|
| **LLM App** | ChatGPT, Claude.ai | LangChain, LangGraph, Dify | DSPy |
| **Agent** | Claude Code, Codex, OpenCode | smolagents (thin), **KohakuTerrarium** | - |
| **Multi-Agent** | - | **KohakuTerrarium** | CrewAI, AutoGen |

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

## Why the split matters

A lot of systems blur internal agent logic and external multi-agent wiring into one abstraction. KohakuTerrarium keeps them separate on purpose.

<table>
  <tr>
    <td valign="top" width="50%">
      <strong>Inside a creature</strong><br>
      One controller delegates to tools and sub-agents.<br>
      This is the agent-level abstraction.
    </td>
    <td valign="top" width="50%">
      <strong>Between creatures</strong><br>
      Independent creatures communicate through channels.<br>
      This is the multi-agent wiring layer.
    </td>
  </tr>
</table>

That separation lets you:

- build a creature once and run it solo or in a team
- change tools, prompts, triggers, or outputs without redesigning the whole system
- reason about single-agent behavior separately from team topology
- keep the multi-agent layer simple instead of turning it into another hidden controller

## Architecture at a glance

### Terrarium view

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
                    ├-------┬----------┬--------┤
                    |  swe  | reviewer |  ....  |
                    +-------┴----------┴--------+
```

### Creature view

```text
    List, Create, Delete  +------------------+
                    +-----|   Tools System   |
      +---------+   |     +------------------+
      |  Input  |   |          ^        |
      +---------+   V          |        v
        |   +---------+   +------------------+   +------------+
        +-->| Trigger |-->|    Controller    |-->| Sub Agents |
User input  | System  |   |    (Main LLM)    |<--| with tools |
            +---------+   +------------------+   +------------+
                ^             |          |
                |             v          v
                |         +--------+  +------+
                +---------|Channels|  |Output|
                 Receive  +--------+  +------+
                             |  ^
                             v  |
                          +------------------+
                          | Other Creatures  |
                          +------------------+
```

## Quick start

```bash
# Install from PyPI
pip install kohakuterrarium

# Or install from source (for development)
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
pip install -e ".[dev]"

# Build the web frontend for `kt web` / `kt app`
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend

# Install the default creatures, terrariums, and plugins
kt install https://github.com/Kohaku-Lab/kt-defaults.git

# Set up LLM access (pick one)
kt login codex          # Codex OAuth (no API key needed)
kt model default gpt-5.4  # or configure any OpenAI-compatible API

# Run a single creature
kt run @kt-defaults/creatures/swe

# Run a multi-agent terrarium
kt terrarium run @kt-defaults/terrariums/swe_team

# Launch the web dashboard
kt serve
```

Supports OpenRouter, OpenAI, Anthropic, Google Gemini, and any OpenAI-compatible API.

## Choose your path

### I want to run something now

- Start with [Getting Started](docs/guides/getting-started.md)
- Browse [CLI Reference](docs/reference/cli.md)
- See included [examples](examples/README.md)

### I want to build my own creature

- Read [Creatures](docs/guides/creatures.md)
- Read [Configuration Reference](docs/guides/configuration.md)
- See [Custom Modules](docs/guides/custom-modules.md)
- See [Plugins](docs/guides/plugins.md)

### I want to build a terrarium

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

A creature is a standalone agent with its own runtime, tools, sub-agents, prompts, and state.

You can run it directly:

```bash
kt run path/to/creature
```

### Terrarium

A terrarium is a composition layer that wires creatures together through channels and manages their lifecycle.

It does not add a second reasoning loop.

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
- package installation for creatures and terrariums
- Python embedding through `Agent`, `TerrariumRuntime`, and `KohakuManager`
- HTTP and WebSocket serving
- web dashboard and native desktop app
- custom module and plugin systems

## Programmatic usage

Use agents and terrariums as libraries — your code is the orchestrator:

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

Compose agents with Python operators — `>>` (sequence), `&` (parallel), `|` (fallback), `*` (retry), `async for` (loop):

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

- `kt-defaults/` contains installable default creatures and terrariums
- `examples/agent-apps/` contains config-driven examples
- `examples/code/` contains Python usage examples
- `examples/terrariums/` contains multi-agent examples
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
kt-defaults/                   # Installable defaults package
examples/                      # Example agents, terrariums, code samples, plugins
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
