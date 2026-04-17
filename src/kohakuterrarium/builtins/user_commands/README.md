# builtins/user_commands/

Built-in slash commands (`/help`, `/clear`, `/compact`, `/model`, `/plugin`,
`/regen`, `/status`, `/exit`). One command per file, each decorated with
`@register_user_command(name)`. Discovery uses the same pattern as
`tool_catalog` — import the module and the decorator registers the class.

## Files

| File | Command | Layer | Aliases | Purpose |
|------|---------|-------|---------|---------|
| `__init__.py` | — | — | — | Catalog (`register_user_command`, `get_builtin_user_command`, `list_builtin_user_commands`); imports every command to trigger registration |
| `help.py` | `/help` | INPUT | `h`, `?` | List available commands (`ui_list` payload) |
| `exit.py` | `/exit` | INPUT | `quit`, `q` | Graceful shutdown (`ui_confirm`) |
| `clear.py` | `/clear` | AGENT | — | Wipe conversation context; emit `context_cleared` event (`ui_confirm` + `ui_notify`) |
| `compact.py` | `/compact` | AGENT | — | Trigger manual context compaction now (`ui_notify`) |
| `model.py` | `/model [name]` | AGENT | `llm` | List models or switch profile (`ui_select` / `ui_notify`) |
| `plugin.py` | `/plugin` | AGENT | `plugins` | List plugins or toggle enable/disable (`ui_select`) |
| `regen.py` | `/regen` | AGENT | `regenerate` | Regenerate the last assistant response with current settings |
| `status.py` | `/status` | AGENT | `info` | Show agent/session status (`ui_info_panel`) |

## Dependency direction

Imported by: `core/agent.py` (via `_init_user_commands`), `builtins/cli_rich/completer.py`
(for tab completion), `api/routes/configs.py` (to surface the list over
HTTP), `cli/__init__.py` indirectly.

Imports: `modules/user_command/base` (`BaseUserCommand`, `CommandLayer`,
`UserCommandContext`, `UserCommandResult`, `ui_*` payload constructors).

## Command layers

- **INPUT** — intercepted by the input module before the LLM ever sees the
  text. No agent state required. Fast (no LLM call). Used for `/help`,
  `/exit`.
- **AGENT** — handled by the agent with full state access. Used for
  `/clear`, `/compact`, `/model`, `/plugin`, `/regen`, `/status`.

The `layer` class attribute determines which pathway executes the command.

## Key entry points

- `register_user_command(name)` — decorator for custom commands
- `get_builtin_user_command(name_or_alias)` — factory; returns an instance
- `list_builtin_user_commands()` — sorted canonical names

## Notes

- Commands return `UserCommandResult(output=..., data=..., consumed=bool)`.
  `output` is the plain-text rendering (CLI/TUI); `data` is a structured
  UI payload built via `ui_text` / `ui_notify` / `ui_select` /
  `ui_info_panel` / `ui_list` / `ui_confirm` that the web frontend renders
  natively. See `../../modules/user_command/README.md` for the protocol.
- `clear` emits a `context_cleared` event AND saves a snapshot so the
  session store can undo-stack the clear if needed.
- `regen` works by popping the last assistant turn + refiring the user
  event — it reuses normal rewind machinery on the agent
  (`core/agent_messages.py`).
- `plugin` toggles go through the agent's `PluginManager`; disabled
  plugins are still loaded so they can be re-enabled without restart.

## See also

- `../../modules/user_command/README.md` — protocol, layer enum, UI payloads
- `../cli_rich/completer.py` — slash-command tab completion
- `../../core/agent_messages.py` — rewind / regenerate plumbing
