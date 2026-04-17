# bootstrap/

Bootstrap factories for agent component initialization. Each module owns one
focused factory that creates one agent subsystem from an `AgentConfig`,
reducing the import fan-out of the agent constructor. Factories handle
builtin, custom-path, and package module types with graceful fallbacks on
failure (a tool that fails to import doesn't kill the agent).

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Package marker |
| `agent_init.py` | `AgentInitMixin` — orchestrates the init-order dance (llm → registry → executor → subagents → output → controller → input → user_commands → triggers → plugins) |
| `llm.py` | LLM provider factory (Codex OAuth, OpenAI-compatible, profile resolution) |
| `tools.py` | Tool instance creation and registry wiring (builtin / path / package) |
| `subagents.py` | Sub-agent config resolution; register into `SubAgentManager` + `Registry` |
| `triggers.py` | Trigger creation (timer, context, channel, custom) and registration |
| `io.py` | Input and output module factories with fallback to CLI/stdout |
| `plugins.py` | Plugin discovery + load (config-declared + package-declared), returns a `PluginManager` |

## Dependency direction

Imported by `core/agent.py` via `AgentInitMixin`. Imports from `core/`
(config, loader, registry, session, trigger_manager), `builtins/` (catalogs
and io modules), `llm/`, `modules/*`, `packages.py`, and `utils/logging`.

Nothing inside `bootstrap/` imports anything outside these — it is a thin
wiring layer with no runtime loops of its own.

## Key entry points

- `AgentInitMixin` — mixed into `Agent`; defines `_init_*` helpers
- `bootstrap.llm.create_llm_provider(config) -> LLMProvider`
- `bootstrap.tools.build_tool_registry(...)` / `bootstrap.tools.register_tool(...)`
- `bootstrap.subagents.register_subagents(...)`
- `bootstrap.triggers.create_triggers(...)`
- `bootstrap.plugins.load_plugins(...)`

## Notes

- Factories MUST NOT raise on partial failure; log and continue so the agent
  boots with a degraded feature set rather than crashing.
- `plugins.py` is the newest addition — it walks both the agent's `plugins:`
  config list and package-declared `plugins/` directories to produce a
  unified `PluginManager`.
- Initialization order matters (see `agent_init.py`): the controller cannot
  start until LLM + registry + executor + subagents + output exist, and
  triggers cannot fire until the controller is wired.

## See also

- `../core/README.md` — how `AgentInitMixin` composes with the rest of Agent
- `../modules/plugin/` — plugin protocol and manager
- `plans/inventory-runtime.md` §1 — agent lifecycle
