<p align="center">
  <img src="images/banner.png" alt="KohakuTerrarium" width="800">
</p>
<p align="center">
  <strong>The machine for building agents, so you stop rebuilding the machine every time you want a new one.</strong>
</p>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-KohakuTerrarium--1.0-green" alt="License">
  <img src="https://img.shields.io/badge/version-2.0.0-orange" alt="Version">
</p>

<p align="center">
  <strong>English</strong> &nbsp;·&nbsp; <a href="README.zh.md">繁體中文</a> &nbsp;·&nbsp; <a href="README.zh-CN.md">简体中文</a>
</p>
<p align="center">
  <a href="https://terrarium.kohaku-lab.org"><strong>Documentation</strong></a>
</p>

---

## See it run (60 seconds)

```bash
pip install kohakuterrarium                 # install
kt login codex                              # authenticate a provider
kt install @kt-biome                        # get the official creature pack
kt run @kt-biome/creatures/swe --mode cli   # run a full coding agent
```

That's an interactive shell with a complete coding agent: file tools, shell access, web search, sub-agents, resumable sessions. `Ctrl+D` exits; `kt resume --last` picks back up exactly where you stopped.

The same agent, as a library, is four lines:

```python
from kohakuterrarium import Agent

agent = await Agent.build("@kt-biome/creatures/swe")
await agent.start()
result = await agent.run("Explain what this codebase does.")  # -> TurnResult
print(result.text, result.usage)
```

Want more hand-holding? [Getting Started](docs/en/guides/getting-started.md). Want to build your own? [First Creature](docs/en/tutorials/first-creature.md). Want to embed it? [Programmatic Usage](docs/en/guides/programmatic-usage.md).

## Is this for you?

**You probably want KohakuTerrarium if** you need a new agent shape and don't want to rebuild the substrate; you want strong out-of-the-box agents you can customise; you want to drive agents from your own Python (batch jobs, bots, pipelines); your requirements are still evolving.

**You probably don't if** an existing agent product (Claude Code, Codex, …) already fits your stable needs; your mental model doesn't map onto controller / tools / triggers / sub-agents / channels; you need sub-50 ms per-operation latency. More honesty at [boundaries](docs/en/concepts/boundaries.md).

## What KohakuTerrarium is

KohakuTerrarium is a framework for building agents, not another agent.

The last two years produced a striking number of agent products: Claude Code, Codex, Gemini CLI, OpenCode, OpenClaw, Hermes Agent, and many more. They are genuinely different products, and they all re-implement the same substrate from scratch: a controller loop, tool dispatch, triggers, sub-agents, sessions, persistence, multi-agent wiring. Every new agent shape costs a ground-up reimplementation of the plumbing.

KohakuTerrarium puts that substrate in one place, so the next agent shape costs a config file and a few custom modules, not a new repo.

The core abstraction is the **creature**: a standalone agent with its own controller, tools, sub-agents, triggers, memory, and I/O. Creatures are hosted by a **Terrarium** engine: a graph runtime that owns channels, lifecycle, output wiring, hot-plug, and the topology + session bookkeeping that follows graph changes. A **Studio** layer sits above that for catalog, identity, active sessions, persistence, and the web/desktop/API management surfaces. Optionally, a **Laboratory** transport layer splits host and engine across machines. Studio and Terrarium stay unchanged; a WebSocket hop is slotted in between.

Everything is Python. Agents are objects you `await`, with typed results. They embed inside your tools, your bots, your batch jobs, and even inside other agents.

For out-of-the-box creatures you can try today, see [**kt-biome**](https://github.com/Kohaku-Lab/kt-biome), the official pack of useful agents and plugins built on the framework.

## Where it fits

|  | Product | Framework | Utility / Wrapper |
|--|---------|-----------|-------------------|
| **LLM App** | ChatGPT, Claude.ai | LangChain, LangGraph, Dify | DSPy |
| **Agent** | ***kt-biome***, Claude Code, Codex, OpenCode, OpenClaw, Hermes Agent… | ***KohakuTerrarium***, smolagents | (none) |
| **Multi-Agent** | ***kt-biome*** | ***KohakuTerrarium*** | CrewAI, AutoGen |

Most tooling sits below the agent layer, or jumps straight to multi-agent orchestration with a thin idea of what an agent is. KohakuTerrarium starts with the agent itself.

A creature is made of:

- **Controller**: the reasoning loop
- **Input**: how events enter the agent
- **Output**: how results leave the agent
- **Tools**: what actions it can take
- **Triggers**: what wakes it up
- **Sub-agents**: internal delegation for specialised tasks

A terrarium composes multiple creatures horizontally through channels, lifecycle management, and observability.

## Key features

- **Agent-level abstraction.** The six-module creature model is the first-class concept. A new agent shape is "write a config and maybe a few custom modules," not "rebuild the runtime."
- **A real Python API.** `Agent.build`, typed `TurnResult` turns with timeouts that actually cancel, streaming typed events, `@kt.tool` to turn any function into an agent tool, direct LLM-instance injection, strict-by-default errors instead of silent fallbacks.
- **Built-in session persistence and resume.** The engine mints and owns session files (`session=` / `Terrarium(session_dir=)`); resume hours later with `kt resume` or `Terrarium.resume`. `SessionReader` replays any finished run offline.
- **Searchable session history.** Every event is indexed. `kt search` and the `search_memory` tool let you (and the agent) look up past work.
- **Non-blocking context compaction.** Long-running agents keep working while context compacts in the background.
- **Comprehensive built-in tools and sub-agents.** File, shell, web, JSON, notebook, search, editing, planning, review, research, plus the `group_*` graph-editor tools on privileged nodes.
- **MCP support.** Connect stdio / streamable-HTTP MCP servers per-agent or globally; four meta-tools keep the prompt small no matter how many servers you attach.
- **Package system + marketplace.** `kt install @name` resolves through [TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket); `kohakuterrarium.packages.ensure("@name")` is the idempotent script-side primitive.
- **Composition algebra.** `>>`, `&`, `|`, `*`, `.iterate` operators for stitching agents into pipelines.
- **Multiple runtime surfaces.** CLI, TUI, web dashboard, and native desktop app out of the box.
- **Optional four-layer auth.** Host token, admin password, and multi-user accounts. Opt in per layer; the default is everything off. See [Authentication](docs/en/guides/authentication.md).

## Quick start

> **Recommended Python version**: 3.12 or newer. CI validates 3.12+; 3.10 and 3.11 still install and run (`requires-python = ">=3.10"`) but are supported best-effort.

### 1. Install KohakuTerrarium

```bash
# From PyPI
pip install kohakuterrarium
# Optional extras: pip install "kohakuterrarium[full]"

# Or from source (for development; uv is the project convention)
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"

# Build the web frontend (required for `kt web` / `kt app` from source)
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

### 2. Install OOTB creatures and plugins

```bash
kt install @kt-biome                 # official pack, via TerrariumMarket
kt marketplace search                # browse everything listed
kt install <git-url>                 # any third-party package by URL
kt install ./my-creatures -e         # editable local install
```

See [`docs/en/guides/packages.md`](docs/en/guides/packages.md) for sources, version pinning, and env overrides.

### 3. Authenticate a model provider

```bash
kt login codex                       # Codex OAuth (ChatGPT subscription)
kt model default gpt-5.4
# Or API-key providers via `kt config key set <provider>`
```

Supports Codex OAuth, OpenRouter/OpenAI, native Anthropic, Google Gemini, Kimi Code, GLM Coding Plan, and any OpenAI-compatible API.

### 4. Run something

```bash
kt run @kt-biome/creatures/swe --mode cli       # single creature
kt terrarium run @kt-biome/terrariums/swe_team  # multi-agent team
kt serve start                                  # web dashboard
kt app                                          # native desktop
kt doctor                                       # check your setup
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
- [Python API reference](docs/en/reference/python.md)

### I want to understand what's going on

- [Concept docs](docs/en/concepts/README.md)
- [Glossary](docs/en/concepts/glossary.md): plain-English definitions
- [Why KohakuTerrarium](docs/en/concepts/foundations/why-kohakuterrarium.md)
- [What is an agent](docs/en/concepts/foundations/what-is-an-agent.md)

### I want to deploy it

- [Docker deployment](docs/en/guides/deployment-docker.md): AIO, host + workers, distributed compose recipes
- [systemd deployment](docs/en/guides/deployment-systemd.md): `kt service install` + hardened units
- [Reverse-proxy deployment](docs/en/guides/deployment-reverse-proxy.md): nginx / Cloudflare Tunnel + TLS
- [Laboratory](docs/en/guides/laboratory.md): multi-node lab-host / lab-client model

### I want to work on the framework itself

- [Development home](docs/en/dev/README.md)
- [Internals](docs/en/dev/internals.md)
- [Testing](docs/en/dev/testing.md)
- [`AGENTS.md`](AGENTS.md): the one-file brief for coding agents working on or with this framework
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

### Runtime hierarchy

```text
User / API / Desktop
        |
        v
+----------------------+     no reasoning loop
| Studio / App Layer   |  catalog, identity, active sessions,
|                      |  persistence, attach, editors, live traces
+----------------------+
        |
        v
+----------------------+     optional: only in multi-node mode
| Laboratory (Lab)     |  WebSocket transport + custom envelope,
|                      |  spans the host across N worker machines
+----------------------+     transparent to Studio + Terrarium
        |
        v
+----------------------+     no LLM; owns structure
| Terrarium Engine     |  creature graph, topology, channels,
|                      |  hot-plug, output wiring, session
|                      |  merge / split bookkeeping
+----------+-----------+
           |
   +-------+----------------+
   |                        |
Privileged node         Worker creatures
(user-facing, group     swe / coder / reviewer / ...
 tools, designated by
 recipe `root:`)
   |
   v
Sub-agents inside each creature
(vertical/private delegation)
```

- **Studio** is the management framework used by the web dashboard, desktop app, and HTTP API. It owns catalog views, identity/settings, active sessions, persistence, attach/resume, editors, and live traces. It does not reason.
- **Laboratory (Lab)** is the optional network layer between Studio and Terrarium. In single-machine mode it is not even imported. In `--mode lab-host` one host coordinates creatures on N worker machines over WebSocket; Studio and Terrarium don't change. See the [Laboratory concept](docs/en/concepts/laboratory.md) and [guide](docs/en/guides/laboratory.md).
- **Terrarium** is the runtime engine that hosts every running creature in the process. A standalone agent is a one-creature graph; a team is a connected graph. The engine runs no LLM, but owns *structure*: which creatures share a component, which channels exist, where turn-end output is delivered, which session store backs which graph, and the auto-merge / auto-split bookkeeping that follows topology changes.
- **Privileged node** is a creature granted the `group_*` tools (graph editor: spawn / remove creatures, draw / delete channels, start / stop members). The recipe `root:` keyword promotes one node and applies the standard user-facing wiring; privilege can also be granted inline (`privileged: true`) or imperatively (`is_privileged=True`).
- **Creature** owns reasoning: controller, tools, triggers, sub-agents, plugins, memory, I/O, prompts, private state. Creatures don't need to know whether they are alone or part of a graph.
- **Sub-agents** are vertical/private delegation inside one creature. Prefer them when one controller can decompose the task internally; use Terrarium when peer creatures need horizontal cooperation.

### Channels and output wiring

- **Channel**: a named broadcast pipe. Every listener receives every send. Use it for conditional, optional, or observed traffic.
- **Output wiring**: deterministic pipeline edges that auto-deliver a creature's turn-end output to named targets, no `send_message` required.

### Modules

A creature has six conceptual modules. **Five are user-extensible**: you swap their implementations in config or Python. The sixth, the controller, is the reasoning loop that drives them.

| Module | What it does | Example custom use |
|--------|---------------|--------------------|
| **Input** | Receives external events | Discord listener, webhook, voice input |
| **Output** | Delivers agent output | Discord sender, TTS, file writer |
| **Tool** | Executes actions | API calls, database access, RAG retrieval |
| **Trigger** | Generates automatic events | Timer, scheduler, channel watcher |
| **Sub-agent** | Delegated task execution | Planning, code review, research |

Plus **plugins**, which modify the connections *between* modules without forking them (prompt plugins, lifecycle hooks, gating). See the [plugins guide](docs/en/guides/plugins.md).

### Environment and session

- **Environment**: shared terrarium state (shared channels).
- **Session**: private creature state (scratchpad, private channels, sub-agent state).

Private by default, shared by opt-in.

## Programmatic usage

Agents are async Python values with typed results. Three surfaces, smallest to largest:

Start with a bare agent. Build it, run a turn, read a `TurnResult`:

```python
import asyncio
from kohakuterrarium import Agent, tool

@tool
def count_words(text: str) -> str:
    """Count the words in a text."""
    return str(len(text.split()))

async def main():
    agent = await Agent.build(
        "@kt-biome/creatures/general",
        llm="default",            # profile name, preset, or a provider instance
        tools=[count_words],      # plain functions become agent tools
    )
    await agent.start()
    result = await agent.run("How many words in the README?", timeout=300)
    print(result.status, result.text, result.usage)   # failures are typed, not silent
    await agent.stop()

asyncio.run(main())
```

Then the engine, which hosts many creatures with their own working dirs and persistent sessions:

```python
from kohakuterrarium import Terrarium

async with Terrarium() as engine:
    worker = await engine.add_creature(
        "@kt-biome/creatures/swe",
        llm="fast",                          # bad name? raises here, not mid-run
        pwd=workdir,                         # per-creature cwd, no global chdir
        session=workdir / "run.kohakutr",    # engine mints + closes the store
    )
    result = await worker.run("Fix the failing test.", timeout=1800)
```

One engine hosts 60 creatures as comfortably as one. See [`examples/code/batch_grading.py`](examples/code/batch_grading.py) for the full batch pattern in about 50 lines, and [`SessionReader`](docs/en/reference/python.md) to replay any run afterwards.

And the composition algebra, which builds pipelines over agents and plain callables:

```python
from kohakuterrarium.compose import agent, factory

async with await agent("@kt-biome/creatures/swe") as swe:
    result = await (swe >> extract_code >> reviewer)(task)

# Operators: >> (sequence), & (parallel), | (fallback), * (retry)
safe = (expert * 2) | generalist
results = await (analyst & writer & designer)(task)

async for draft in (writer >> reviewer).iterate(task):
    if "APPROVED" in draft:
        break
```

More: [Programmatic Usage](docs/en/guides/programmatic-usage.md), [Composition](docs/en/guides/composition.md), [Python API](docs/en/reference/python.md), and [`examples/code/`](examples/).

## Runtime surfaces

### CLI and TUI

- **cli**: rich inline terminal experience
- **tui**: full-screen Textual application
- **plain**: simple stdout/stdin for pipes and CI

See [CLI Reference](docs/en/reference/cli.md).

### Web dashboard

Vue-based dashboard + FastAPI server backed by the Studio management layer.

```bash
kt web                       # one-shot, foreground
kt serve start               # long-running daemon
# Frontend dev: npm run dev --prefix src/kohakuterrarium-frontend
```

See [HTTP API](docs/en/reference/http.md), [Serving guide](docs/en/guides/serving.md), [Frontend Architecture](docs/en/dev/frontend.md).

### Desktop app

`kt app` launches the web UI inside a native desktop window (requires `pywebview`).

### Deployment (Docker / systemd / multi-node)

Three first-party Docker images on GHCR. Pick the shape:

```bash
# AIO: lab-host + an embedded worker in one container
docker run -d -p 8001:8001 -v kt:/home/kt/.kohakuterrarium \
  ghcr.io/kohaku-lab/kohakuterrarium:2.0.0

# Host + workers (different boxes): two images, same shared token
docker run -d -p 8001:8001 -p 8100:8100 \
  -e KT_HOST_TOKEN=$TOKEN ghcr.io/kohaku-lab/kohakuterrarium-host:2.0.0
docker run -d -e KT_HOST_URL=ws://host:8100 -e KT_HOST_TOKEN=$TOKEN \
  -e KT_CLIENT_NAME=worker-a ghcr.io/kohaku-lab/kohakuterrarium-client:2.0.0
```

Prefer systemd? Install a hardened native service in one command:

```bash
sudo kt service install --all                              # AIO unit
sudo kt service install --host                             # host unit
sudo kt service install --client --name worker-a --host-url ws://… --host-token …
sudo systemctl enable --now kohakuterrarium-host kohakuterrarium-client@worker-a
```

Ready-to-use compose files under `examples/deployment/` (AIO, host + workers, distributed) and an nginx template for TLS termination. `/healthz` + `/readyz` endpoints drive Docker `HEALTHCHECK` and reverse-proxy active health.

See [Docker deployment](docs/en/guides/deployment-docker.md), [systemd deployment](docs/en/guides/deployment-systemd.md), and [reverse-proxy deployment](docs/en/guides/deployment-reverse-proxy.md).

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

The agent can search its own history via the `search_memory` tool, and Python can replay any run:

```python
from kohakuterrarium import SessionReader

with SessionReader("runs/student-42.kohakutr") as r:
    for turn in r.turns():
        print(turn.user_text, "->", turn.assistant_text[:80], turn.tool_calls)
```

`.kohakutr` files store conversation, tool calls, events, scratchpad, sub-agent state, channel messages, jobs, resumable triggers, and config metadata.

See [Sessions](docs/en/guides/sessions.md), [Memory](docs/en/guides/memory.md).

## Packages, defaults, and examples

Creatures are meant to be packaged, installed, reused, and shared.

```bash
kt install @kt-biome                              # marketplace
kt install https://github.com/someone/pack.git    # git URL
kt install ./my-creatures -e                      # editable local
kt list
kt update --all
```

Run installed configs with package references, and use them from Python:

```bash
kt run @cool-creatures/creatures/my-agent
kt terrarium run @cool-creatures/terrariums/my-team
```

```python
from kohakuterrarium import packages

packages.ensure("@kt-biome")   # idempotent; safe at the top of any script
```

Available resources:

- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome): official creatures, terrariums, and plugin pack
- `examples/agent-apps/`: config-driven creature examples
- `examples/code/`: Python usage examples
- `examples/terrariums/`: multi-agent examples
- `examples/plugins/`: plugin examples

See [examples/README.md](examples/README.md).

## Codebase map

```text
src/kohakuterrarium/
  core/              # Agent runtime: controller, turn API, executor, events, environment
  bootstrap/         # Agent initialisation factories (LLM, tools, I/O, triggers, plugins)
  cli/               # `kt` command dispatcher
  studio/            # Management facade: catalog, identity, sessions, persistence, attach, editors
  terrarium/         # Runtime engine: creature graph, topology, channels, output wiring, hot-plug
  builtins/          # Built-in tools, sub-agents, I/O modules, TUI, user commands, CLI UI
  builtin_skills/    # Markdown skill manifests for on-demand docs
  session/           # Session persistence, SessionReader, memory search, embeddings
  serving/           # Launch/transport helpers
  api/               # FastAPI HTTP + WebSocket adapters over Studio and Terrarium
  compose/           # Composition algebra primitives
  mcp/               # MCP client manager
  modules/           # Base protocols for tools, inputs, outputs, triggers, sub-agents, user commands
  llm/               # LLM providers, profiles, API key management
  parsing/           # Tool-call parsing and stream handling
  prompt/            # Prompt aggregation, plugins, skill loading
  errors.py          # The typed exception hierarchy (KTError and friends)
  validate.py        # Pre-flight checks behind `kt doctor`
  testing/           # Test infrastructure (ScriptedLLM, TestAgentBuilder, recorders)

src/kohakuterrarium-frontend/   # Vue web frontend
examples/                       # Example creatures, terrariums, code samples, plugins
docs/                           # Tutorials, guides, concepts, reference, dev
```

Every subpackage has its own README describing files, dependency direction, and invariants.

## Documentation map

Full docs live in [`docs/`](docs/en/README.md).

### Tutorials
[First Creature](docs/en/tutorials/first-creature.md) · [First Terrarium](docs/en/tutorials/first-terrarium.md) · [First Python Embedding](docs/en/tutorials/first-python-embedding.md) · [First Custom Tool](docs/en/tutorials/first-custom-tool.md) · [First Plugin](docs/en/tutorials/first-plugin.md)

### Guides
[Getting Started](docs/en/guides/getting-started.md) · [Creatures](docs/en/guides/creatures.md) · [Terrariums](docs/en/guides/terrariums.md) · [Sessions](docs/en/guides/sessions.md) · [Memory](docs/en/guides/memory.md) · [Configuration](docs/en/guides/configuration.md) · [Programmatic Usage](docs/en/guides/programmatic-usage.md) · [Composition](docs/en/guides/composition.md) · [Custom Modules](docs/en/guides/custom-modules.md) · [Plugins](docs/en/guides/plugins.md) · [MCP](docs/en/guides/mcp.md) · [Packages](docs/en/guides/packages.md) · [Serving](docs/en/guides/serving.md) · [Laboratory](docs/en/guides/laboratory.md) · [Docker deployment](docs/en/guides/deployment-docker.md) · [systemd deployment](docs/en/guides/deployment-systemd.md) · [Reverse-proxy deployment](docs/en/guides/deployment-reverse-proxy.md) · [Examples](docs/en/guides/examples.md)

### Concepts
[Glossary](docs/en/concepts/glossary.md) · [Why KohakuTerrarium](docs/en/concepts/foundations/why-kohakuterrarium.md) · [What is an agent](docs/en/concepts/foundations/what-is-an-agent.md) · [Composing an agent](docs/en/concepts/foundations/composing-an-agent.md) · [Modules](docs/en/concepts/modules/README.md) · [Agent as a Python object](docs/en/concepts/python-native/agent-as-python-object.md) · [Composition algebra](docs/en/concepts/python-native/composition-algebra.md) · [Multi-agent](docs/en/concepts/multi-agent/README.md) · [Patterns](docs/en/concepts/patterns.md) · [Boundaries](docs/en/concepts/boundaries.md)

### Reference
[CLI](docs/en/reference/cli.md) · [HTTP](docs/en/reference/http.md) · [Python API](docs/en/reference/python.md) · [Configuration](docs/en/reference/configuration.md) · [Builtins](docs/en/reference/builtins.md) · [Plugin hooks](docs/en/reference/plugin-hooks.md)

## Roadmap

Near-term directions include more reliable terrarium flow, richer UI output / interaction modules across CLI / TUI / web, more built-in creatures, plugins, and integrations, and better daemon-backed workflows for long-running and remote usage. See [ROADMAP.md](ROADMAP.md).

## Contributing

- [Contributing guide](CONTRIBUTING.md)
- [Development home](docs/en/dev/README.md)
- [Testing](docs/en/dev/testing.md)
- [Internals](docs/en/dev/internals.md)
- [Frontend architecture](docs/en/dev/frontend.md)

## License

[KohakuTerrarium License 1.0](LICENSE): based on Apache-2.0 with naming and attribution requirements.

- Derivative works must include `Kohaku` or `Terrarium` in their name.
- Derivative works must provide visible attribution with a link to this project.

Copyright 2024-2026 Shih-Ying Yeh (KohakuBlueLeaf) and contributors.

## Community
- QQ: 1097666427
- Discord: https://discord.gg/xWYrkyvJ2s
- Forum: https://linux.do/

## FAQ

### General

**What is KohakuTerrarium?**
A Python-native framework for building agents. The public hierarchy: **Creature** is the agent unit, **Terrarium** is the runtime engine that owns the creature graph (topology, channels, sessions; no LLM of its own), and **Studio** is the management layer above the engine (catalog, sessions, persistence, API).

**How does it differ from other agent frameworks?**
Responsibilities stay separated: creatures own reasoning and tools, the engine owns graph topology / channels / lifecycle / session bookkeeping, Studio owns management surfaces. Horizontal teams use Terrarium recipes and channels; Python request pipelines use the composition algebra.

### Installation & Setup

**What Python version is required?**
Python 3.10 or higher; **3.12+ is recommended** (that's what CI validates). Install via `pip install kohakuterrarium`.

**Which LLM providers are supported?**
Codex OAuth, OpenAI/OpenRouter-style providers, native Anthropic, Google Gemini, Kimi Code, GLM Coding Plan, local OpenAI-compatible servers (Ollama, vLLM), and other OpenAI-compatible cloud providers. Configure with `kt login`, `kt config llm add`, or provider API keys. `kt doctor` verifies the setup.

**Can I use local models?**
Yes. Point the LLM endpoint at your local server (Ollama, vLLM, etc.) and set the model name in your creature config or an LLM profile.

### Core Concepts

**What is a "Creature"?**
The standalone agent unit: controller, tools, triggers, sub-agents, plugins, memory, I/O, prompts, private state. It runs alone or as a node in a Terrarium graph.

**What is a "Terrarium"?**
The runtime engine that hosts creature graphs. It runs no LLM and has no reasoning loop, but owns the structural decisions: connected components, channel registry, hot-plug, output wiring, session merge / split bookkeeping.

**What are "Plugins"?**
Hook-based extensions that wrap framework behavior: pre/post hooks around tool calls, LLM calls, and sub-agent runs, plus lifecycle callbacks. Sandboxing, budgets, and permission gating all ship as ordinary plugins.

### Development

**How do I create a custom Creature?**
Define a YAML config with tools, prompts, and behavior, or build one in Python with `Agent.build` / `engine.add_creature`. See [First Creature](docs/en/tutorials/first-creature.md).

**Can I embed agents in my Python application?**
Yes, and it is a first-class surface. `await agent.run(...)` returns a typed `TurnResult`; `run_stream` yields typed events; the engine handles working dirs, sessions, and many concurrent creatures. See [`examples/code/`](examples/code/) and the [Programmatic Usage guide](docs/en/guides/programmatic-usage.md).

**How does multi-agent composition work?**
Use Terrarium recipes / channels / output wiring for horizontal teams. Use `compose` for lightweight Python-side request pipelines (`>>`, `&`, `|`, retry) when you don't need a long-lived graph.

### Troubleshooting

**Why is my creature not responding?**
Run `kt doctor` first. It checks provider auth, profile resolution, and config validity in one shot. Then check network connectivity and API key validity.

**How do I debug agent behavior?**
Use `kt run --verbose` for detailed logs. Resume or inspect prior work with `kt resume`, search it with `kt search`, replay it with `SessionReader`, or use the Studio session viewer in the web/desktop UI.

**Where can I get help?**
- QQ Group: 1097666427
- Discord: https://discord.gg/xWYrkyvJ2s
- Forum: https://linux.do/
