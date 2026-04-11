# KohakuTerrarium Documentation

## Guide

Practical how-to guides for building agents and terrariums.

- [Getting Started](guide/getting-started.md): install, configure, run your first agent
- [Configuration Reference](guide/configuration.md): agent YAML, terrarium YAML, all fields
- [Creatures](guide/creatures.md): pre-built creatures, inheritance, creating your own
- [Terrariums](guide/terrariums.md): multi-agent setup, channel wiring, root agent binding
- [Sessions](guide/sessions.md): persistence, resume, `.kohakutr` files
- [Programmatic Usage](guide/programmatic-usage.md): embed agents in scripts, composition algebra, KohakuManager
- [Custom Modules](guide/custom-modules.md): build custom tools, inputs, outputs, triggers, sub-agents
- [Plugins](guide/plugins.md): intercept agent flows with prompt and lifecycle plugins
- [Frontend Layout](guide/frontend-layout.md): split tree layout, presets, edit mode, panels, shortcuts
- [Examples](guide/examples.md): walkthrough of included example agents and terrariums

## Concepts

Conceptual foundations and architecture internals: what the abstractions are, how they relate, and how the system works under the hood.

- [Overview](concepts/overview.md): five major systems, event model, composition levels
- [Agents](concepts/agents.md): creature lifecycle, controller as orchestrator, sub-agents
- [Terrariums](concepts/terrariums.md): pure wiring layer, root agent, horizontal composition
- [Channels](concepts/channels.md): queue/broadcast types, channel triggers, on_send callbacks
- [Execution Model](concepts/execution.md): event sources, processing loop, tool modes
- [Prompt System](concepts/prompts.md): system prompt aggregation, skill modes, topology injection
- [Serving Layer](concepts/serving.md): KohakuManager, unified WebSocket, session recording
- [Environment-Session](concepts/environment.md): isolation, shared state, session lifecycle
- [Tool Formats](concepts/tool-formats.md): call syntax, parsing, format configuration

## API Reference

Lookup tables for specific methods, endpoints, and commands.

- [Python API](api-reference/python.md): Agent, SessionStore, TerrariumRuntime, all modules
- [HTTP API](api-reference/http.md): REST + unified WebSocket + config discovery
- [CLI Reference](api-reference/cli.md): kt run, kt resume, kt terrarium run, kt login

## Contributing

Work on the framework itself.

- [Testing](develop/testing.md): test infrastructure, unit/integration coverage, behavior docs
- [Framework Internals](develop/internals.md): import analysis, internal decisions, technical notes
- [Frontend Architecture](develop/frontend.md): component tree, stores, WebSocket protocols, adding panels
