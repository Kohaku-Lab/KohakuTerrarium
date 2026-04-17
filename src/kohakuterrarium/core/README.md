# core/ (Runtime and Orchestration)

The core module contains the runtime engine that powers every KohakuTerrarium
agent. All components communicate through a unified `TriggerEvent` model.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Package marker; re-exports for convenience |
| `agent.py` | `Agent` class public API (`from_path`, `run`, `start`, `stop`, `inject_input`) |
| `agent_handlers.py` | `AgentHandlersMixin` — event processing + controller loop orchestration |
| `agent_tools.py` | `AgentToolsMixin` — tool dispatch, direct/background result collection |
| `agent_runtime_tools.py` | Lower-level tool dispatch helpers used by `AgentToolsMixin` |
| `agent_messages.py` | `AgentMessagesMixin` — edit / regenerate / rewind past messages |
| `backgroundify.py` | `BackgroundifyHandle` / `PromotionResult` — mid-flight direct→background task promotion |
| `controller.py` | `Controller` + `ControllerConfig` — LLM conversation loop + event queue |
| `conversation.py` | `Conversation` context manager (message list, truncation, system prompt) |
| `executor.py` | Background tool runner; `asyncio.create_task()` during streaming |
| `events.py` | `TriggerEvent`, `EventType`, constructors (`create_tool_complete_event`, etc.) |
| `channel.py` | `AgentChannel`, `ChannelMessage`, `ChannelRegistry` — named pub/sub channels |
| `compact.py` | Non-blocking auto-compact (`CompactManager`) — background summarization of the old context zone |
| `session.py` | `Session` — keyed shared state registry (channels, scratchpad, TUI, extras) |
| `environment.py` | `Environment` — inter-creature state per user request |
| `scratchpad.py` | Session-scoped key-value working memory (framework-managed, cheap) |
| `termination.py` | `TerminationConditions` — max_turns / max_tokens / max_duration / idle / keywords |
| `config.py` | `load_agent_config` / `build_agent_config` — YAML / JSON / TOML + env interpolation |
| `config_types.py` | Config dataclasses (`AgentConfig`, `InputConfig`, `ControllerConfig`, …) |
| `constants.py` | Framework magic numbers (truncation limits, status preview lengths) |
| `trigger_manager.py` | `TriggerManager` — owns trigger instances + async tasks, hot-add/remove |
| `job.py` | `JobStore`, `JobResult`, `JobState` — job status tracking |
| `loader.py` | `ModuleLoader` — dynamic import of custom tools / inputs / outputs / subagents |
| `registry.py` | `Registry` — generic module registry for tools and sub-agents |

## Dependency direction

- Leaves: `constants`, `events`, `config_types`, `backgroundify`, `job`,
  `scratchpad`, `session`, `environment`, `channel`, `termination`, `registry`,
  `loader` (each imports only `utils/` + stdlib).
- Mid-layer: `controller`, `conversation`, `executor`, `compact`,
  `trigger_manager`, `config`.
- Top: `agent.py` + its mixins (`agent_handlers.py`, `agent_tools.py`,
  `agent_runtime_tools.py`, `agent_messages.py`). These also mix in
  `AgentInitMixin` from `../bootstrap/agent_init.py`.

Imports across package boundaries: `core/` is imported by almost everything
(`bootstrap/`, `builtins/`, `terrarium/`, `serving/`, `api/`, `compose/`),
but NEVER imports them back.

## Key entry points

- `Agent.from_path(path, …)` — construct an agent from a config folder
- `Agent.run()` — main event loop; drives input → controller → tools
- `TriggerEvent` (in `events.py`) — the single event type that flows through the system
- `Controller` — LLM conversation loop
- `CompactManager` (in `compact.py`) — background context compaction
- `TriggerManager` — runtime trigger add/remove

## Dependency diagram

```
    agent.py  (mixes in Init / Handlers / Tools / Messages)
        │
        ├── bootstrap/*              (factories)
        ├── controller.py ─── conversation.py
        │       │
        │       ├── parsing/         (stream parser)
        │       └── llm/             (provider)
        │
        ├── agent_tools.py ─── agent_runtime_tools.py ─── backgroundify.py
        │       │
        │       └── executor.py ─── job.py
        │
        ├── compact.py               (runs alongside controller)
        ├── trigger_manager.py       (fires TriggerEvents into controller)
        └── session.py + environment.py + channel.py + scratchpad.py
                │
                └── events.py + config_types.py + constants.py (leaves)
```

## Notes

- Three Agent mixins (handlers / tools / messages) exist only to keep file
  sizes under the 600-line cap. They are not independently useful.
- `compact.py` is non-blocking: the agent keeps producing output during
  summarization; the splice happens atomically when the summary lands.
- `backgroundify.py` lets a direct-mode tool promote itself to background
  mid-flight (e.g. long bash that exceeded its expected budget), returning
  a `PromotionResult` to the controller while the task keeps running.

## See also

- `../bootstrap/README.md` — how the init factories plug into `AgentInitMixin`
- `plans/inventory-runtime.md` §1–§3 — lifecycle, controller loop, tool pipeline
- `docs/concepts/foundations/` — event model + execution semantics
