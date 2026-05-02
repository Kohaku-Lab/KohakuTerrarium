# builtins/outputs/

Built-in output module implementations. Output modules deliver agent output
to destinations such as the terminal, TTS engines, or the TUI. The
`__init__.py` provides a registry with factory functions parallel to the
input registry. TUI output is registered at import time.

## Files

| File          | Description                                                                                |
| ------------- | ------------------------------------------------------------------------------------------ |
| `__init__.py` | Output registry, factory functions, builtin registration                                   |
| `stdout.py`   | `StdoutOutput` (plain terminal) and `PrefixedStdoutOutput` (with agent name prefix)        |
| `tts.py`      | TTS base classes (`TTSModule`, `TTSConfig`) and implementations (`ConsoleTTS`, `DummyTTS`) |

## Registered Types

`stdout`, `stdout_prefixed`, `console_tts`, `dummy_tts`, `tui` (from `builtins.tui`)

## Dependencies

- `kohakuterrarium.modules.output.base` (BaseOutputModule)
- `kohakuterrarium.builtins.tui.output` (TUIOutput, registered at import)
- `kohakuterrarium.utils.logging`
