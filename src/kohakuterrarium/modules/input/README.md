# modules/input/

Input module protocol and base class. Input modules receive external input
(CLI, API, ASR, TUI) and convert it into `TriggerEvent` objects for the
controller. This package defines only the interface; concrete implementations
live in `builtins/inputs/`.

## Files

| File          | Description                                                                                               |
| ------------- | --------------------------------------------------------------------------------------------------------- |
| `__init__.py` | Re-exports `InputModule` protocol and `BaseInputModule` ABC                                               |
| `base.py`     | `InputModule` protocol (`start`, `stop`, `get_input`) and `BaseInputModule` with running-state management |

## Dependencies

- `kohakuterrarium.core.events` (TriggerEvent)
