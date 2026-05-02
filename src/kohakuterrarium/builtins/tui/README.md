# builtins/tui/

Full-screen terminal UI for agent interaction, built on the Textual framework.
Provides a split-pane layout with an output area, tabbed side panel (status,
logs), and an input prompt with line editing. The TUI input and output modules
share a `TUISession` instance via the `Session` registry, enabling coordinated
terminal access for a single agent or a group of cooperating agents.

## Files

| File          | Description                                                                       |
| ------------- | --------------------------------------------------------------------------------- |
| `__init__.py` | Exports `TUISession`                                                              |
| `session.py`  | `TUISession`: shared state (Textual app, input queue, output buffer, stop signal) |
| `input.py`    | `TUIInput`: input module that reads from the TUI app's input widget               |
| `output.py`   | `TUIOutput`: output module that writes to the TUI app with visual turn separation |

## Dependencies

- `kohakuterrarium.core.events` (TriggerEvent)
- `kohakuterrarium.core.session` (get_session)
- `kohakuterrarium.modules.input.base` (BaseInputModule)
- `kohakuterrarium.modules.output.base` (BaseOutputModule)
- `kohakuterrarium.utils.logging`
- Third-party: `textual`, `rich`
