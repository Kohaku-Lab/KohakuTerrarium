# Guides

Task-oriented docs for people running, building with, and extending KohakuTerrarium.

If you want the mental model, go to [Concepts](../concepts/README.md).
If you want exact fields, flags, or signatures, go to [Reference](../reference/README.md).
If you want a guided first walk-through, go to [Tutorials](../tutorials/README.md).

## Start here

- [Getting Started](getting-started.md) — install, authenticate, run your first creature, resume, and open the web UI.
- [Creatures](creatures.md) — anatomy, inheritance, prompt files, tool/subagent wiring, packaging.
- [Sessions](sessions.md) — `.kohakutr` files, resume, compaction.

## Build and configure

- [Configuration](configuration.md) — task-oriented "how do I configure X" recipes.
- [Creatures](creatures.md) — authoring standalone agents.
- [Plugins](plugins.md) — prompt and lifecycle plugins.
- [Custom Modules](custom-modules.md) — writing tools, inputs, outputs, triggers, sub-agents.
- [MCP](mcp.md) — registering MCP servers per-agent or globally.
- [Packages](packages.md) — `kohaku.yaml` manifests, install modes, publishing.

## Multi-agent and composition

- [Terrariums](terrariums.md) — channels, root agents, hot-plug, observers.
- [Composition](composition.md) — `>>`, `&`, `|`, `*` pipelines from Python.
- [Programmatic Usage](programmatic-usage.md) — embedding `Agent`, `AgentSession`, `TerrariumRuntime`, `KohakuManager`.

## Persist and search

- [Sessions](sessions.md) — persistence model and resume.
- [Memory](memory.md) — embedding providers, FTS and vector search, `search_memory` tool.

## Run it somewhere

- [Serving](serving.md) — `kt web`, `kt app`, the `kt serve` daemon.
- [Frontend Layout](frontend-layout.md) — web dashboard panels and presets.

## Learn from code

- [Examples](examples.md) — what every folder under `examples/` demonstrates.

## See also

- [Concepts](../concepts/README.md) — why things work the way they do.
- [Reference](../reference/README.md) — exhaustive lookup.
- [Development](../dev/README.md) — contributing to the framework itself.
