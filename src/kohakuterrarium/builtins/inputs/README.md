# builtins/inputs/

Built-in input module implementations. Input modules receive external input
(terminal, API) and produce `TriggerEvent` objects for the controller.
The `__init__.py` provides a registry with factory functions so that
`bootstrap/io.py` can create input modules by name. Audio/ASR examples live
under `examples/` and are loaded as custom modules when explicitly configured.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Input registry, factory functions, builtin registration |
| `cli.py` | `CLIInput` (blocking terminal) and `NonBlockingCLIInput` (non-blocking polling) |
| `none.py` | `NoneInput` for trigger-only agents with no user input |


## Registered Types

`cli`, `cli_nonblocking`, `none`, `tui` (from `builtins.tui`)

## Dependencies

- `kohakuterrarium.core.events` (TriggerEvent, create_user_input_event)
- `kohakuterrarium.modules.input.base` (BaseInputModule)
- `kohakuterrarium.builtins.tui.input` (TUIInput, registered at import)
- `kohakuterrarium.utils.logging`
