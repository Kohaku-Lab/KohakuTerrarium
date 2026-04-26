# builtins/tools/

Built-in tool implementations. Each tool extends `BaseTool` and uses the
`@register_builtin` decorator for automatic registration in the tool catalog.
The `__init__.py` imports every module to trigger registration and re-exports
the public API from `builtins.tool_catalog`.

## Files

| File | Registered names / description |
|------|--------------------------------|
| `__init__.py` | Imports all tools (triggers registration), re-exports catalog API |
| `registry.py` | Backward-compatible re-exports from `builtins.tool_catalog` |
| `bash.py` | `bash` (shell commands) and `python` (subprocess python) |
| `read.py` | `read`: read file contents with optional line range |
| `write.py` | `write`: create or overwrite files |
| `edit.py` | `edit`: single-diff edit with guard rails; exports `check_edit_guards` / `build_result_diff` used by `multi_edit` |
| `multi_edit.py` | `multi_edit`: atomic or policy-driven batch of ordered search/replace edits on one file |
| `notebook_read.py` / `notebook_edit.py` | `notebook_read` / `notebook_edit`: cell-aware Jupyter `.ipynb` read and batch edit tools |
| `notebook_utils.py` | Shared notebook parsing, cell-id, output-summary, and mutation helpers |
| `glob.py` | `glob`: find files by pattern |
| `grep.py` | `grep`: ripgrep-backed regex search with type filtering |
| `tree.py` | `tree`: list directory structure (.gitignore-aware, line-limited) |
| `scratchpad_tool.py` | `scratchpad`: session-scoped key-value working memory |
| `ask_user.py` | `ask_user`: request human input mid-execution |
| `json_read.py` / `json_write.py` | `json_read` / `json_write` with path expressions |
| `send_message.py` | `send_message`: send to a named channel |
| `info.py` | `info`: load full documentation for a tool or sub-agent on demand |
| `stop_task.py` | `stop_task`: cancel a running background tool, sub-agent, or trigger by id |
| `search_memory.py` | `search_memory`: FTS5 + semantic search over the current session's event log |
| `web_fetch.py` | `web_fetch`: clean-read a URL (crawl4ai → trafilatura → jina → naive fallback) |
| `web_search.py` | `web_search`: DuckDuckGo search (optional `duckduckgo-search` dep) |
| `terrarium_lifecycle.py` | `terrarium_create` / `terrarium_status` / `terrarium_stop` |
| `terrarium_messaging.py` | `terrarium_send` / `terrarium_observe` / `terrarium_history` |
| `terrarium_creature.py` | `creature_start` / `creature_stop` / `creature_interrupt` |

## Dependency direction

Imported by `bootstrap/tools.py` (via the catalog) when a creature config
names a builtin tool. Imports:

- `kohakuterrarium.builtins.tool_catalog` (`register_builtin`)
- `kohakuterrarium.modules.tool.base` (`BaseTool`, `ToolResult`, `ToolContext`, `ExecutionMode`)
- `kohakuterrarium.core.channel` / `core.session` (channel + scratchpad tools)
- `kohakuterrarium.terrarium.*` (terrarium_* and creature_* tools — lazy-loaded
  via `terrarium/tool_registration.py` to avoid circular imports)
- `kohakuterrarium.session.memory` / `session.store` (`search_memory`)
- `kohakuterrarium.utils.logging`

No tool imports another tool's implementation except `multi_edit` and
`notebook_edit` reusing `edit.py` result/guard helpers.

## Key entry points

- `@register_builtin("name")` on a `BaseTool` subclass
- `get_builtin_tool(name)` from `builtins.tool_catalog`

## Notes

- Terrarium tools register lazily via
  `terrarium/tool_registration.ensure_terrarium_tools_registered()` to break
  the `core ← builtins ← terrarium ← core` cycle.
- Web tools degrade gracefully when optional deps (crawl4ai, trafilatura,
  duckduckgo-search) aren't installed — they log a warning and return a
  useful `ToolResult.error`.
- Setup-able triggers (`add_timer`, `watch_channel`, `add_schedule`) are
  not defined here — they come from
  `modules/trigger/callable.py:CallableTriggerTool` wrapping each
  `universal = True` trigger class. A creature opts in per-entry with
  `- { name: add_timer, type: trigger }` in its `tools:` list. Installed
  triggers run against the agent's live `TriggerManager` and persist to
  the session store on resume.

## See also

- `../README.md` — full builtin catalog (tools + subagents + io + TUI)
- `../../modules/tool/` — `BaseTool` protocol + execution modes
- `../../builtin_skills/` — full-doc markdown loaded via the `info` tool
- `../../terrarium/README.md` — terrarium management tool context
