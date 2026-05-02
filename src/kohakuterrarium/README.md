# kohakuterrarium package

Root package for the KohakuTerrarium agent framework.

## Top-Level Files

- `__init__.py` — public exports (`Studio`, `Terrarium`, `Creature`, events), version info
- `__main__.py` — CLI entry point (`python -m kohakuterrarium ...`)
- `__briefcase__.py` — Briefcase desktop-app bootstrap
- `packages.py` — Package manager (install / uninstall / edit extension packages)
- `registry.json` — Bundled curated package registry (used by `kt install`)

## Runtime hierarchy

- `core/` owns the creature/agent runtime: controller, executor, events,
  conversation, tools, triggers, sub-agents, plugins, compact, and session state.
- `terrarium/` owns the no-LLM graph runtime for running creatures: topology,
  channels, lifecycle, output wiring, hot-plug, engine events, and session attach.
- `studio/` owns management concerns above the engine: catalog, identity,
  active sessions, saved-session persistence, attach policies, and editor flows.
- `api/`, `cli/`, the web dashboard, and desktop app are adapters over
  Studio/Terrarium plus launch/UI glue.

## Subpackages

| Package           | Purpose                                                                                       |
| ----------------- | --------------------------------------------------------------------------------------------- |
| `core/`           | Agent, Controller, Executor, events, config, session, registry, compact, runtime tools        |
| `bootstrap/`      | Agent initialization factories (LLM, tools, IO, subagents, triggers, plugins)                 |
| `builtins/`       | Built-in tools, sub-agents, inputs, outputs, TUI, rich-CLI, user commands                     |
| `builtin_skills/` | On-demand markdown documentation for tools and sub-agents                                     |
| `modules/`        | Base classes and protocols (input, output, tool, trigger, subagent, user_command, plugin)     |
| `terrarium/`      | `Terrarium` engine, `Creature` handle, graph topology, channels, output wiring, engine events |
| `studio/`         | Management facade: catalog, identity, active sessions, persistence, attach, editors           |
| `compose/`        | Agent composition algebra (`>>`, `&`, `\|`, `*`) for Python-side pipelines                   |
| `mcp/`            | MCP client manager + meta-tools for external MCP servers                                      |
| `serving/`        | Web/desktop launch helpers and legacy compatibility wrappers                                  |
| `api/`            | FastAPI HTTP + WebSocket adapters over Studio and Terrarium                                   |
| `cli/`            | `kt` command dispatcher (run / resume / web / model / config / ...)                           |
| `session/`        | Session persistence via KohakuVault (.kohakutr files) + memory/FTS5/vector search             |
| `llm/`            | LLM provider abstraction (OpenAI-compatible, Codex OAuth, native Anthropic, presets, profiles) |
| `parsing/`        | Streaming state machine for LLM output (bracket, XML, native)                                 |
| `prompt/`         | System prompt aggregation, Jinja2 templating, plugin/skill loading                            |
| `commands/`       | Framework commands executed inline during LLM streaming (read, info, jobs, wait)              |
| `testing/`        | Test infrastructure (ScriptedLLM, TestAgentBuilder, output recorders)                         |
| `utils/`          | Shared utilities (structured logging, async helpers, file safety guards)                      |

## Dependency flow

```text
api/, cli/, frontend, desktop
          |
          v
       studio/          serving/ (launch + legacy compatibility)
          |
          v
      terrarium/
          |
          v
bootstrap/ + builtins/ + modules/
          |
          v
        core/
          |
          v
parsing/ + prompt/ + llm/ + session/ + mcp/ + utils/
```

Key principles:

- `utils/` is a leaf: imported by everything, imports nothing from the framework.
- `modules/` defines protocols/base classes and stays implementation-light.
- `core/` must never import `terrarium/`, `studio/`, `api/`, or `cli/`.
- `terrarium/` may import `core/` and bootstrap/builtin factories; it does not make decisions or call LLMs itself.
- `studio/` sits above `terrarium/` and centralizes user-facing management policies.
- `api/` and `cli/` are top-layer adapters; they should delegate management work to `studio/` and runtime work to `terrarium/`.
- Zero runtime import cycles are verified via `scripts/dep_graph.py` and `tests/unit/test_dep_graph_lint.py`.

## See also

- `docs/en/concepts/studio.md` — Studio management layer.
- `docs/en/concepts/multi-agent/terrarium.md` — Terrarium runtime model.
- `docs/en/guides/programmatic-usage.md` — public Python embedding surface.
