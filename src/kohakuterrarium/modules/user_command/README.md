# modules/user_command/

Protocol + base class for user slash commands typed by the human. Commands
execute in one of two layers (INPUT or AGENT) and return both plain text
(for CLI/TUI) and structured UI payloads (for web frontends).

## Responsibility

Defines the contract implementations must satisfy. Provides UI payload
constructors (`ui_text`, `ui_notify`, `ui_select`, `ui_info_panel`,
`ui_list`, `ui_confirm`) so any implementation can emit structured data
without importing frontend code. No runtime, no registry — those live in
`builtins/user_commands`.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Package marker (intentionally empty — consumers import from `.base`) |
| `base.py` | `UserCommand` protocol, `BaseUserCommand` abstract class, `CommandLayer` enum, `UserCommandContext`, `UserCommandResult`, `parse_slash_command`, and the `ui_*` payload constructors |

## Dependency direction

Imported by: `builtins/user_commands/*` (every slash command), `core/agent.py`
(user-command dispatch plumbing), `api/routes/configs.py` (payload type
surface), `builtins/cli_rich/completer.py` (command listing), frontend
integration layers.

Imports: stdlib only (`abc`, `dataclasses`, `enum`, `typing`). This is a
leaf module.

## Key entry points

- `BaseUserCommand` — subclass this for concrete commands (provides
  `execute` with built-in error wrapping; override `_execute`)
- `CommandLayer.INPUT` / `CommandLayer.AGENT` — where the command runs
- `UserCommandContext` — passed to `_execute`; holds `agent`, `session`,
  `input_module`, `output_fn`, `extra`
- `UserCommandResult` — return type; `output`, `data`, `consumed`, `error`
- `parse_slash_command(text)` — `"/model gpt-5"` → `("model", "gpt-5")`
- UI payload constructors — return plain dicts that frontends switch on
  via `data["type"]`:
  - `ui_text(message)` — plain text block
  - `ui_notify(message, level=...)` — toast (info / success / warning / error)
  - `ui_confirm(message, action=..., action_args=...)` — Yes/No dialog
  - `ui_select(title, options, current=..., action=...)` — picker
  - `ui_info_panel(title, fields)` — key/value card
  - `ui_list(title, items)` — styled list
  - reserved: `ui_table`, `ui_form`, `ui_progress` (not yet implemented)

## Notes

- `layer` is a class attribute, not a method — read it without
  instantiating the command.
- `BaseUserCommand.execute` wraps `_execute` in a try/except that converts
  any raised exception into `UserCommandResult(error=str(e))`. Subclasses
  should implement `_execute` and let unexpected errors propagate.
- Returning `consumed=False` lets the command's output fall through to
  the LLM as additional user text — rare, but useful for commands that
  want to prepend context to the next turn.
- UI payloads MUST be JSON-serializable. The CLI / TUI ignore `data` and
  print `output`; the web frontend renders `data` natively.

## See also

- `../../builtins/user_commands/README.md` — the 8 builtin implementations
- `../../core/agent.py` — `_init_user_commands` and dispatch
- `../../api/routes/configs.py` — HTTP surface for the payloads
