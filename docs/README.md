# KohakuTerrarium Documentation

KohakuTerrarium is a framework for building real agents, not just LLM wrappers.

The core abstraction is the **creature**: a standalone agent with its own controller, tools, sub-agents, triggers, memory, and I/O. A creature can run by itself, inherit from another creature, or be packaged and reused directly. A **terrarium** is the optional multi-agent wiring layer that composes creatures through channels.

This means the docs should be read creature-first:

1. What can I run right now?
2. How do I use or customize a creature?
3. How do packages, session memory, and runtime surfaces work?
4. If I want multi-agent composition, how do terrariums fit in?
5. Where do I look up exact commands, APIs, and config fields?
6. How do I work on the framework itself?

## Start here

- New to the project: [Getting Started](guides/getting-started.md)
- I want useful OOTB agents: [`kt-defaults`](../kt-defaults/README.md)
- Building or customizing a creature: [Guides / Creatures](guides/creatures.md)
- Sessions, resume, and searchable memory: [Guides / Sessions](guides/sessions.md)
- Optional multi-agent composition: [Guides / Terrariums](guides/terrariums.md)
- Embedding in Python: [Guides / Programmatic Usage](guides/programmatic-usage.md)
- Learning the architecture: [Concepts](concepts/README.md)
- Looking up commands or APIs: [Reference](reference/README.md)
- Working on the framework: [Development](dev/README.md)

## Documentation hierarchy

### Tutorials

Step-by-step learning paths for readers who want a guided way into the system.

- [Tutorials home](tutorials/README.md)
- [First Creature](tutorials/first-creature.md)
- [First Terrarium](tutorials/first-terrarium.md)
- [First Python Embedding](tutorials/first-python-embedding.md)

### Guides

Task-oriented documentation for using, configuring, extending, and operating creatures, packages, sessions, and terrariums.

- [Guides home](guides/README.md)
- [Getting Started](guides/getting-started.md)
- [Creatures](guides/creatures.md)
- [Sessions](guides/sessions.md)
- [Configuration](guides/configuration.md)
- [Programmatic Usage](guides/programmatic-usage.md)
- [Custom Modules](guides/custom-modules.md)
- [Plugins](guides/plugins.md)
- [Terrariums](guides/terrariums.md)
- [Frontend Layout](guides/frontend-layout.md)
- [Examples](guides/examples.md)

### Concepts

Mental models and architectural explanations.

- [Concepts home](concepts/README.md)
- [Overview](concepts/overview.md)
- [Agents](concepts/agents.md)
- [Terrariums](concepts/terrariums.md)
- [Channels](concepts/channels.md)
- [Execution Model](concepts/execution.md)
- [Prompt System](concepts/prompts.md)
- [Plugins and Extensibility](concepts/plugins.md)
- [Composition Algebra](concepts/composition-algebra.md)
- [Serving Layer](concepts/serving.md)
- [Environment and Session](concepts/environment.md)
- [Tool Formats](concepts/tool-formats.md)

### Reference

Exact lookup docs for commands, APIs, and interfaces.

- [Reference home](reference/README.md)
- [CLI Reference](reference/cli.md)
- [HTTP API](reference/http.md)
- [Python API](reference/python.md)

### Development

Contributor-facing docs for working on the codebase itself.

- [Development home](dev/README.md)
- [Testing](dev/testing.md)
- [Framework Internals](dev/internals.md)
- [Frontend Architecture](dev/frontend.md)

## Codebase hierarchy

The codebase itself is organized by runtime subsystem, not by reader intent. The main packages are:

```text
src/kohakuterrarium/
  core/           # Agent runtime, controller, executor, events, environment
  bootstrap/      # Agent initialization factories for LLM, tools, I/O, triggers
  cli/            # CLI command handlers
  terrarium/      # Multi-agent runtime, topology wiring, hot-plug, persistence
  builtins/       # Built-in tools, sub-agents, I/O modules, TUI, user commands
  builtin_skills/ # Markdown skill manifests for on-demand docs
  session/        # Session persistence, memory search, embeddings
  serving/        # Transport-agnostic service manager and event streaming
  api/            # FastAPI HTTP and WebSocket server
  modules/        # Base protocols for tools, inputs, outputs, triggers, sub-agents
  llm/            # LLM providers, profiles, API key management
  parsing/        # Tool-call parsing and streaming
  prompt/         # Prompt assembly, aggregation, plugins, skill loading
  testing/        # Test infrastructure
```

Many of these packages also include local `README.md` files. Those package-local READMEs are the maintainer-facing side of the docs system. They explain file responsibilities, dependency direction, and subsystem internals near the code.

## Recommended reading order

### If you are evaluating the project

1. [Root README](../README.md)
2. [Getting Started](guides/getting-started.md)
3. [`kt-defaults`](../kt-defaults/README.md)
4. [Examples](guides/examples.md)
5. [Overview](concepts/overview.md)

### If you are building with the framework

1. [Getting Started](guides/getting-started.md)
2. [Creatures](guides/creatures.md)
3. [Sessions](guides/sessions.md)
4. [Plugins and Extensibility](concepts/plugins.md)
5. [Programmatic Usage](guides/programmatic-usage.md)
6. [Terrariums](guides/terrariums.md)

### If you are contributing to the framework

1. [Development home](dev/README.md)
2. [Testing](dev/testing.md)
3. [Framework Internals](dev/internals.md)
4. package READMEs under `src/kohakuterrarium/`
