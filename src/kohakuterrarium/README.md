# kohakuterrarium

Root package for the KohakuTerrarium agent framework.

## Top-Level Files

- `__init__.py` -- Package marker, version info
- `__main__.py` -- CLI entry point (`python -m kohakuterrarium ...`)
- `__briefcase__.py` -- Briefcase desktop-app bootstrap
- `packages.py` -- Package manager (install / uninstall / edit extension packages)
- `registry.json` -- Bundled curated package registry (used by `kt install`)

## Subpackages

| Package | Purpose |
|---------|---------|
| `core/` | Agent, Controller, Executor, events, config, session, registry, compact, runtime tools |
| `bootstrap/` | Agent initialization factories (llm, tools, io, subagents, triggers, plugins) |
| `builtins/` | Built-in tools, sub-agents, inputs, outputs, TUI, rich-CLI, user commands |
| `builtin_skills/` | On-demand markdown documentation for tools and sub-agents |
| `modules/` | Base classes and protocols (input, output, tool, trigger, subagent, user_command, plugin) |
| `terrarium/` | Multi-agent runtime: config, factory, hot-plug, observer, `TerrariumAPI` |
| `compose/` | Agent composition algebra (`>>`, `&`, `|`, `*`) over `AgentSession` |
| `mcp/` | MCP client manager + meta-tools for external MCP servers |
| `serving/` | Transport-agnostic service layer (KohakuManager, AgentSession, web.py) |
| `api/` | FastAPI HTTP + WebSocket server (REST routes + ws streaming) |
| `cli/` | `kt` command dispatcher (run / resume / web / model / config / ...) |
| `session/` | Session persistence via KohakuVault (.kohakutr files) + memory/FTS5/vector search |
| `llm/` | LLM provider abstraction (OpenAI SDK, Codex OAuth, presets, profiles) |
| `parsing/` | Streaming state machine for LLM output (bracket, XML, native) |
| `prompt/` | System prompt aggregation, Jinja2 templating, plugin/skill loading |
| `commands/` | Framework commands executed inline during LLM streaming (read, info, jobs, wait) |
| `testing/` | Test infrastructure (ScriptedLLM, TestAgentBuilder, output recorders) |
| `utils/` | Shared utilities (structured logging, async helpers, file safety guards) |

## Dependency Flow

```
             CLI (cli/) ────────► api/ ────► serving/
                 │                                │
            core/agent ◄─── compose/              │
           /    │     \                           │
   bootstrap  controller  terrarium/runtime ──────┘
      │          │              │
   builtins    parsing    terrarium/factory
      │          │              │
   modules     llm        core/agent
      │          │
      └── mcp/ ──┘
          │
         utils (leaf)
```

Key principles:
- `utils/` is a leaf: imported by everything, imports nothing from the framework
- `builtins/tool_catalog` and `builtins/subagent_catalog` are leaf modules for registration
- `bootstrap/` reduces `core/agent_init` fan-out by encapsulating factory logic
- `terrarium/` imports `core/` but core never imports terrarium (one-way dependency)
- `api/` depends on `serving/` (KohakuManager) and never the other way around
- `cli/` is the user-facing entry; it may import any subsystem but nothing imports `cli/`
- Zero runtime import cycles (verified via `scripts/dep_graph.py`)

## See also

- `docs/concepts/` — mental model (creature vs terrarium, event model, composition)
- `plans/inventory-runtime.md` — detailed runtime flow inventory
