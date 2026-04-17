# KohakuTerrarium Documentation

KohakuTerrarium is a framework for building real agents — not just LLM wrappers.

The first-class abstraction is the **creature**: a standalone agent with its own controller, tools, sub-agents, triggers, prompts, and I/O. A creature runs by itself, inherits from another creature, or ships inside a package. A **terrarium** is the optional multi-agent wiring layer composing creatures through channels. Everything is Python — you can embed any of it in your own code.

These docs are split into four stacks: tutorials (guided), guides (task-oriented), concepts (mental models), and reference (exhaustive lookup). Pick whichever matches where you are.

## Pick a path

| You are... | Start here |
|---|---|
| **Evaluating the project** | [Getting Started](guides/getting-started.md) · [What is an agent](concepts/foundations/what-is-an-agent.md) · [`kt-defaults`](../kt-defaults/README.md) |
| **Operating the CLI / dashboard** | [Getting Started](guides/getting-started.md) · [Serving](guides/serving.md) · [CLI Reference](reference/cli.md) |
| **Building a creature** | [Creatures](guides/creatures.md) · [Configuration](guides/configuration.md) · [Custom Modules](guides/custom-modules.md) |
| **Embedding in Python** | [Programmatic Usage](guides/programmatic-usage.md) · [Composition](guides/composition.md) · [Python API](reference/python.md) |
| **Contributing to the framework** | [Development](dev/README.md) · [Framework Internals](dev/internals.md) · [Testing](dev/testing.md) |

## Documentation structure

### Tutorials

Step-by-step learning paths.

- [First Creature](tutorials/first-creature.md)
- [First Terrarium](tutorials/first-terrarium.md)
- [First Python Embedding](tutorials/first-python-embedding.md)

### Guides

Task-oriented docs: "how do I do X".

- [Getting Started](guides/getting-started.md) — install, authenticate, run, resume.
- [Creatures](guides/creatures.md) — anatomy, inheritance, packaging.
- [Terrariums](guides/terrariums.md) — multi-agent wiring and root agents.
- [Sessions](guides/sessions.md) — `.kohakutr` persistence and resume.
- [Memory](guides/memory.md) — FTS, semantic, hybrid search over session history.
- [Configuration](guides/configuration.md) — task-oriented "how do I configure X".
- [Programmatic Usage](guides/programmatic-usage.md) — `Agent`, `AgentSession`, `TerrariumRuntime`, `KohakuManager`.
- [Composition](guides/composition.md) — `>>`, `&`, `|`, `*` pipelines.
- [Custom Modules](guides/custom-modules.md) — tools, inputs, outputs, triggers, sub-agents.
- [Plugins](guides/plugins.md) — prompt and lifecycle plugins.
- [MCP](guides/mcp.md) — Model Context Protocol servers.
- [Packages](guides/packages.md) — `kohaku.yaml`, install modes, publishing.
- [Serving](guides/serving.md) — `kt web`, `kt app`, `kt serve` daemon.
- [Frontend Layout](guides/frontend-layout.md) — dashboard panels and presets.
- [Examples](guides/examples.md) — tour of the `examples/` tree.

### Concepts

Mental models — why things are the way they are. The concept docs teach the model, not the field list; they assume you want to understand, not just configure.

- [Overview](concepts/README.md)
- [Foundations](concepts/foundations/README.md)
- [Modules](concepts/modules/README.md) — controller, input, trigger, tool, sub-agent, output, channel, plugin, memory, session.
- [Multi-agent](concepts/multi-agent/README.md) — terrariums, root agents, channel topology.
- [Python-native](concepts/python-native/README.md) — agents as Python values, composition algebra.
- [Patterns](concepts/patterns.md) — agent-inside-plugin, agent-inside-tool, and related uses.
- [Boundaries](concepts/boundaries.md) — when to ignore the abstraction, when the framework doesn't fit.
- [Implementation notes](concepts/impl-notes/) — stream parsing, prompt aggregation, and other internals.

### Reference

Exhaustive lookup.

- [CLI Reference](reference/cli.md) — every `kt` command and flag.
- [Configuration Reference](reference/configuration.md) — every config field, type, and default.
- [HTTP API](reference/http.md) — REST and WebSocket endpoints.
- [Python API](reference/python.md) — classes, methods, and protocols.
- [Built-ins Catalog](reference/builtins.md) — every shipped tool, sub-agent, I/O module.
- [Plugin Hooks](reference/plugin-hooks.md) — every hook signature.

### Development

For contributors to the framework itself.

- [Development home](dev/README.md)
- [Testing](dev/testing.md)
- [Framework Internals](dev/internals.md)
- [Frontend Architecture](dev/frontend.md)

## Codebase map

The source is organized by runtime subsystem, not by reader intent. Package-local `README.md` files in each subpackage explain internal responsibilities and dependency direction.

```
src/kohakuterrarium/
  core/             Agent runtime, controller, executor, events, environment
  bootstrap/        Initialization factories for LLM, tools, I/O, triggers
  cli/              CLI command handlers
  terrarium/        Multi-agent runtime, topology wiring, hot-plug
  builtins/         Built-in tools, sub-agents, I/O modules, TUI, user commands
  builtin_skills/   Markdown skill manifests for on-demand tool and sub-agent docs
  session/          Persistence, memory search, embeddings
  serving/          Transport-agnostic service manager and event streaming
  api/              FastAPI HTTP and WebSocket server
  modules/          Protocols for tools, inputs, outputs, triggers, sub-agents
  llm/              LLM providers, profiles, API key management
  parsing/          Tool-call parsing and streaming
  prompt/           Prompt assembly, aggregation, plugins, skill loading
  testing/          Test infrastructure

src/kohakuterrarium-frontend/   Vue web frontend
kt-defaults/                    Showcase package — creatures, terrariums, plugins
examples/                       Runnable examples
docs/                           This tree
```

## What the docs promise

- **Guides** tell you how to do X.
- **Concepts** tell you why X works that way.
- **Reference** tells you every X exists.
- **Tutorials** walk you from zero to a first working X.

If a page says "comprehensive", "powerful", or "seamless" — it's probably out of date. File a PR.
