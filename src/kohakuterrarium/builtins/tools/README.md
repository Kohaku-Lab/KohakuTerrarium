# builtins/tools/

Built-in tool implementations. Each tool extends `BaseTool` and uses the
`@register_builtin` decorator for automatic registration in the tool catalog.
The `__init__.py` imports every module to trigger registration and re-exports
the public API from `builtins.tool_catalog`.

## Layout

Tool families are sub-packages with the family prefix dropped; single tools
stay at the top level.

| Path                         | Registered names / description                                                                                    |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `__init__.py`                | Imports all tools (triggers registration), re-exports catalog API                                                 |
| `registry.py`                | Backward-compatible re-exports from `builtins.tool_catalog`                                                       |
| `files/`                     | `read`, `write`, `edit` (guard rails; exports `check_edit_guards` / `build_result_diff`), `multi_edit` (atomic or policy-driven batch of ordered edits), `glob`, `grep` (ripgrep-backed), `tree` (.gitignore-aware) |
| `bash/`                      | `bash` (shell commands) and its Windows backend                                                                   |
| `notebook/`                  | `notebook_read` / `notebook_edit` (cell-aware Jupyter `.ipynb`), shared parsing and mutation `utils`              |
| `web/`                       | `web_fetch` (extractor chain or direct DeepSeek Fetch), `web_search` (DuckDuckGo, Codex subscription, DeepSeek Search / legacy Responses), the DeepSeek adapter |
| `media/`                     | `image_gen`, `video_gen`, `grok_image_gen`, `canvas_image`, `canvas_preview`                                      |
| `app/`                       | `show_app` plus the app `documents` and `hosts` helpers                                                           |
| `python.py`                  | `python`: subprocess python execution                                                                             |
| `scratchpad_tool.py`         | `scratchpad`: session-scoped key-value working memory                                                             |
| `ask_user.py`                | `ask_user`: request human input mid-execution                                                                     |
| `send_message.py`            | `send_message`: send to a named channel                                                                           |
| `info.py`                    | `info`: load full documentation for a tool or sub-agent on demand                                                 |
| `stop_task.py`               | `stop_task`: cancel a running background tool, sub-agent, or trigger by id                                        |
| `search_memory.py`           | `search_memory`: FTS5 + semantic search over the current session's event log                                      |
| `skill.py`                   | `skill`: load a named skill's full documentation                                                                  |
| `show_card.py`               | `show_card`: structured cards                                                                                     |
| `delete_trigger.py`          | Remove an installed setup-able trigger                                                                            |

Graph management tools (`send_channel`, `group_send`, and the privileged
`group_*` surface) are NOT here; they live in `terrarium/tools/`.

## Dependency direction

Imported by `bootstrap/tools.py` (via the catalog) when a creature config
names a builtin tool. Imports:

- `kohakuterrarium.builtins.tool_catalog` (`register_builtin`)
- `kohakuterrarium.modules.tool.base` (`BaseTool`, `ToolResult`, `ToolContext`, `ExecutionMode`)
- `kohakuterrarium.core.channel` / `core.session` (channel + scratchpad tools)
- `kohakuterrarium.session.memory.index` / `session.store` (`search_memory`)
- `kohakuterrarium.utils.logging`

No tool imports another tool's implementation except `multi_edit` and
`notebook_edit` reusing the `files/edit.py` result/guard helpers.

## Key entry points

- `@register_builtin("name")` on a `BaseTool` subclass
- `get_builtin_tool(name)` from `builtins.tool_catalog`

## Notes

- Web tools degrade gracefully when optional deps (crawl4ai, trafilatura,
  duckduckgo-search) aren't installed; they log a warning and return a
  useful `ToolResult.error`.
- Setup-able triggers (`add_timer`, `watch_channel`, `add_schedule`) are
  not defined here; they come from
  `modules/trigger/callable.py:CallableTriggerTool` wrapping each
  `universal = True` trigger class. A creature opts in per-entry with
  `- { name: add_timer, type: trigger }` in its `tools:` list. Installed
  triggers run against the agent's live `TriggerManager` and persist to
  the session store on resume.

## See also

- `../README.md`: full builtin catalog (tools + subagents + io + TUI)
- `../../modules/tool/`: `BaseTool` protocol + execution modes
- `../../builtin_skills/`: full-doc markdown loaded via the `info` tool
- `../../terrarium/README.md`: terrarium management tool context
