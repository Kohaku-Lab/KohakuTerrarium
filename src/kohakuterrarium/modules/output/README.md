# modules/output/

Output module protocol, base class, and output routing state machine. Output
modules handle the final delivery of agent output to destinations (terminal,
TTS, TUI, API). The `OutputRouter` is a state machine that receives parse
events from the streaming parser and routes text to the correct output module,
suppressing tool blocks and dispatching named output targets.

## Files

| File          | Description                                                                                                     |
| ------------- | --------------------------------------------------------------------------------------------------------------- |
| `__init__.py` | Re-exports protocol, base class, router, and state enum                                                         |
| `base.py`     | `OutputModule` protocol (`write`, `write_stream`, `flush`) and `BaseOutputModule` ABC                           |
| `router.py`   | `OutputRouter` (single-output state machine), `MultiOutputRouter`, `OutputState` enum, `CompletedOutput` record |

## Dependencies

- `kohakuterrarium.parsing` (ParseEvent types: TextEvent, ToolCallEvent, BlockStartEvent, etc.)
- `kohakuterrarium.utils.logging`
