# core/ (Runtime and Orchestration)

The core module contains the runtime engine that powers every KohakuTerrarium
agent. All components communicate through a unified `TriggerEvent` model.

Families of files are packages with the family prefix dropped; single-file
concerns stay at the top level.

## Layout

| Path                  | Responsibility                                                                                           |
| --------------------- | -------------------------------------------------------------------------------------------------------- |
| `agent/`              | `Agent`, assembled from per-concern mixins (`assembly.py`); `turn/`, `tools/`, `history/`; see `agent/README.md` |
| `controller/`         | `controller` (`Controller`: LLM conversation loop + event queue), `context`, `messages`, `metrics`, `plugins`, `recovery`, `inline_upload` |
| `conversation/`       | `conversation` (`Conversation`: message list, truncation, system prompt), `elide`, `sanitize`, `snapshot`, `branch`, `continuation`, `media`, `source`, `locator`, `settled_identity`, `settled_refresh`, `context_snapshot`, `pinned` |
| `compact/`            | `manager` (`CompactManager`: non-blocking background summarization, atomic splice), `branch`, `splice`, `text`, `arguments` |
| `config/`             | `load` (`load_agent_config` / `build_agent_config`), `types` (`AgentConfig`, …), `merge`, `serde`, `resolve` |
| `jobs/`               | `executor` (background tool runner), `job` (`JobStore`, `JobState`), `label`, `single_flight`, `backgroundify` (`BackgroundifyHandle`: direct→background promotion) |
| `tools/`              | `dispatch` (pre-dispatch policy), `output` (normalization), `gate`, `native_validation`, `provider_tools`, `execution_context` |
| `events.py`           | `TriggerEvent`, `EventType`, constructors (`create_tool_complete_event`, …)                              |
| `event_inbox.py`      | Unbounded single-consumer ingress queue for agent events                                                 |
| `pending_input.py`    | Stable identities and edit/cancel helpers for queued input                                               |
| `turn.py`             | `TurnResult` / `TextChunk` / `Activity` / `TurnEnded`: typed turn events + capture                       |
| `channel.py`          | `AgentChannel`, `ChannelMessage`, `ChannelRegistry`: named pub/sub channels                              |
| `session.py`          | `Session`: keyed shared state registry (channels, scratchpad, TUI, extras)                               |
| `environment.py`      | `Environment`: inter-creature state per user request                                                     |
| `scratchpad.py`       | Session-scoped key-value working memory (framework-managed, cheap)                                       |
| `termination.py`      | `TerminationConditions`: max_turns / max_tokens / max_duration / idle / keywords                         |
| `trigger_manager.py`  | `TriggerManager`: owns trigger instances + async tasks, hot-add/remove                                   |
| `output_wiring.py`    | Turn-end event delivery between creatures through configured wiring                                      |
| `budget.py`           | Shared budget primitives for agent / sub-agent loops                                                     |
| `token_ledger.py`     | Per-agent running token totals, context fill and window                                                  |
| `metrics_hook.py`     | Process-wide metrics observation hook                                                                    |
| `constants.py`        | Framework magic numbers (truncation limits, status preview lengths)                                      |
| `loader.py`           | `ModuleLoader`: dynamic import of custom tools / inputs / outputs / subagents                            |
| `registry.py`         | `Registry`: generic module registry for tools and sub-agents                                             |
| `mcp_registry.py`     | Global MCP server registry shared by config assembly and the identity surface                            |

## Dependency direction

- Leaves: `constants`, `events`, `config/types`, `jobs/backgroundify`,
  `jobs/job`, `scratchpad`, `session`, `environment`, `channel`,
  `termination`, `registry`, `loader` (each imports only `utils/` + stdlib).
- Mid-layer: `controller/`, `conversation/`, `jobs/executor`, `compact/`,
  `trigger_manager`, `config/load`.
- Top: `agent/` and its mixins. They also mix in `AgentInitMixin` from
  `../bootstrap/agent_init.py`.

Imports across package boundaries: `core/` is imported by almost everything
(`bootstrap/`, `builtins/`, `terrarium/`, `serving/`, `api/`, `compose/`),
but NEVER imports them back.

## Key entry points

- `await Agent.build(config, …)`: canonical constructor (path, `@pkg/...`
  ref, or `AgentConfig`); `Agent.from_path(path, …)` is the sync low-level
  variant
- `Agent.run(content)`: typed single-turn driver returning `TurnResult`
  (`run_stream` for live events)
- `Agent.run_forever()`: main event loop; drives input → controller → tools
- `TriggerEvent` (in `events.py`): the single event type that flows through the system
- `Controller` (in `controller/controller.py`): LLM conversation loop
- `CompactManager` (in `compact/manager.py`): background context compaction
- `TriggerManager`: runtime trigger add/remove

## Dependency diagram

```
    agent/  (mixes in Init / Construct / Turn / Handlers / Tools / Messages / …)
        │
        ├── bootstrap/*                      (factories)
        ├── controller/ ─── conversation/
        │       │
        │       ├── parsing/                 (stream parser)
        │       └── llm/                     (provider)
        │
        ├── agent/tools/ ─── jobs/backgroundify
        │       │
        │       └── jobs/executor ─── jobs/job
        │
        ├── compact/                         (runs alongside controller)
        ├── trigger_manager.py               (fires TriggerEvents into controller)
        └── session.py + environment.py + channel.py + scratchpad.py
                │
                └── events.py + config/types + constants.py (leaves)
```

## Notes

- `compact/` is non-blocking: the agent keeps producing output during
  summarization; the splice happens atomically when the summary lands.
- `jobs/backgroundify.py` lets a direct-mode tool promote itself to
  background mid-flight (e.g. long bash that exceeded its expected budget),
  returning a `PromotionResult` to the controller while the task keeps running.

## See also

- `../bootstrap/README.md`: how the init factories plug into `AgentInitMixin`
- `docs/en/dev/internals.md` §Agent runtime: lifecycle, controller loop, tool pipeline
- `docs/en/concepts/foundations/`: event model + execution semantics
