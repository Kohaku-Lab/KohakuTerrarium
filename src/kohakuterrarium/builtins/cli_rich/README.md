# builtins/cli_rich/

Inline rich CLI mode — the default interactive experience for `kt run`.
Uses **one** `prompt_toolkit.Application` as the sole renderer at the bottom
of the terminal, with Rich rendering content into ANSI strings that
prompt_toolkit paints.

## Layout (mirroring Ink / ratatui)

```
┌──────────────────────────────────────┐
│   real terminal scrollback           │  ← committed via run_in_terminal()
│   (banner, past user/assistant msgs, │
│    tool-result panels, ...)          │
├──────────────────────────────────────┤  ← top of Application area
│   live status window                 │  ← ANSI-rendered LiveRegion
│   (streaming msg + active tools +    │
│    bg strip + compaction banner)     │
├──────────────────────────────────────┤
│ ┌─ message ──────────────────────┐   │  ← bordered TextArea composer
│ │ ▶ user types here              │   │
│ └────────────────────────────────┘   │
│   in 1.2k · out 567 · model · /help  │  ← single-line footer
└──────────────────────────────────────┘
```

One render loop. `app.invalidate()` schedules a coalesced redraw.
Scrollback output goes through `app.run_in_terminal(cb)` so the cursor
moves above the app area before stdout writes, then the app redraws below.

## Files

| File | Responsibility |
|------|----------------|
| `__init__.py` | Public API (`RichCLIApp`, `RichCLIInput`, `RichCLIOutput`) |
| `app.py` | `RichCLIApp` — the single `prompt_toolkit.Application`; owns layout, key bindings, lifecycle, streaming callbacks |
| `composer.py` | `Composer` — `TextArea` + key bindings (history, multi-line, paste, completer) |
| `completer.py` | Slash-command completer (builtin commands + per-command `get_completions`) |
| `live_region.py` | `LiveRegion` — streaming message + active tool list + background strip + compaction banner → ANSI |
| `commit.py` | `ScrollbackCommitter` / `SessionReplay` — rendering finished blocks into scrollback + replaying session events on resume |
| `runtime.py` | Output backend selection, task spawner with exception trapping, stderr→logger redirection, enhanced-keyboard toggle |
| `theme.py` | Glyphs, colors, spinner frames |
| `input.py` | `RichCLIInput` — stub `InputModule` that sleeps forever (actual input driven by `RichCLIApp`) |
| `output.py` | `RichCLIOutput` — `OutputModule` that routes agent events to `RichCLIApp` callbacks |
| `blocks/footer.py` | `FooterBlock` — single-line bottom footer (tokens, model, mode) |
| `blocks/message.py` | `AssistantMessageBlock` — accumulates streaming text with markdown detection |
| `blocks/tool.py` | `ToolCallBlock` — status + args + output preview; supports sub-agent nesting + background promotion |

## Dependency direction

Imported by: `cli/run.py` (default run mode), `cli/resume.py` (resume mode),
`session/resume.py` (io module wiring).

Imports: `prompt_toolkit` (`Application`, `Layout`, `TextArea`, key bindings,
completion), `rich` (`Console`, `Markdown`, `Panel`, `Syntax`, `Text`);
framework: `core/events`, `modules/input/base`, `modules/output/base`,
`builtins/user_commands` (for completer), `utils/logging`.

## Key entry points

- `RichCLIApp` — orchestrator (most interesting methods: `on_text_chunk`,
  `on_processing_start/end`, `on_tool_*`, `commit_*`)
- `RichCLIInput` / `RichCLIOutput` — the pair plugged into `Agent.input` /
  `Agent.output_router` when `--mode cli`
- `LiveRegion` — where all the in-flight state lives before it is
  committed to scrollback

## Notes

- Input and output are linked: `RichCLIOutput` tells `RichCLIApp` what
  happened, and `RichCLIApp` tells `RichCLIInput` when a line is ready
  (via `app.inject_input_callback`).
- `commit.SessionReplay` rehydrates scrollback from the session event log
  when resuming — finished messages and tool panels print directly (no
  prompt_toolkit app running yet) before `app.run_async` takes over.
- `runtime.StderrToLogger` captures stray stderr from third-party code so
  it doesn't corrupt the live region.
- The app auto-detects terminal color depth and supports the
  enhanced-keyboard protocol on supported terminals (Kitty / iTerm2) for
  chord bindings.

## See also

- `../tui/` — alternative full-screen Textual UI (`--mode tui`)
- `../user_commands/README.md` — slash commands the completer surfaces
- `../../cli/run.py` — where `RichCLIApp` is launched
