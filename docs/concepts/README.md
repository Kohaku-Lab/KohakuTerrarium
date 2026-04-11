# Concepts

Conceptual foundations and architecture internals: what the abstractions are, how they relate, and how the system works under the hood.

- [Overview](overview.md) — major systems, event model, composition levels
- [Agents](agents.md) — creature lifecycle, controller as orchestrator, sub-agents
- [Terrariums](terrariums.md) — pure wiring layer, root agent, horizontal composition
- [Channels](channels.md) — queue and broadcast semantics, channel triggers, observation
- [Execution Model](execution.md) — event sources, processing loop, tool modes
- [Prompt System](prompts.md) — system prompt aggregation, skill modes, topology injection
- [Plugins and Extensibility](plugins.md) — modules as block customization, plugins as connection customization
- [Composition Algebra](composition-algebra.md) — programmatic composition of agentic steps in Python
- [Serving Layer](serving.md) — KohakuManager, unified WebSocket, session recording
- [Environment and Session](environment.md) — isolation, shared state, session lifecycle
- [Tool Formats](tool-formats.md) — call syntax, parsing, format configuration
