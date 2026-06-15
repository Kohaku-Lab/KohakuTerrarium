---
title: Terminal commands (kt-cli / kt-tui)
summary: The standalone interactive front doors, the startup agent picker, and exposing the commands on your PATH with kt shims.
tags:
  - guides
  - cli
  - tui
  - install
---

# Terminal commands: `kt-cli` and `kt-tui`

KohakuTerrarium ships two standalone interactive front doors in addition to
`kt run`:

- **`kt-cli`** — the rich inline CLI (prompt-toolkit). Equivalent to
  `kt run <creature> --mode cli`.
- **`kt-tui`** — the full-screen Textual TUI. Equivalent to
  `kt run <creature> --mode tui`.

Both also exist as subcommands of `kt` (`kt cli`, `kt tui`) for discoverability.
The difference from `kt run` is that the creature argument is **optional** — omit
it and you get an interactive startup picker.

## Pick an agent at startup

Run either command with no argument:

```bash
kt-cli        # rich inline CLI, choose an agent from a list
kt-tui        # full-screen TUI, choose an agent from a list
```

You get an expandable list grouped by source — your local `./creatures` first,
then each installed package — with the creatures and terrariums inside each.
Terrariums (multi-creature teams) are tagged `[team]` and show their member
creatures.

Picker keys (cli):

| Key | Action |
|-----|--------|
| `↑` / `↓` | move the cursor |
| `←` / `→` | collapse / expand a group |
| type any text | filter by name, description, or source |
| `Backspace` | edit the filter |
| `Enter` | run the highlighted creature/terrarium |
| `Esc` / `Ctrl+C` | cancel |

The TUI picker is a standard tree: arrow keys navigate, the box at the top
filters, `Enter` selects, `Esc` cancels.

Selecting a creature starts it solo; selecting a terrarium applies the recipe and
focuses its root. A startup picker needs an interactive terminal — in a
non-interactive context (a pipe, a script), pass the creature explicitly instead.

## Run a specific agent

Give a creature folder, a recipe folder, or a `@pkg/...` reference:

```bash
kt-cli @kt-biome/creatures/general
kt-tui ./terrariums/swe_team
kt-cli general --add critic --channel reviews    # ad-hoc team
```

The full option set (`--llm`, `--session` / `--no-session`, `--log-level`,
`--log-stderr`, `--add`, `--channel`) matches `kt run`, minus `--mode` (the
command name fixes it).

## Put the commands on your PATH: `kt shims`

How you get `kt`, `kt-cli`, and `kt-tui` on your terminal depends on how you
installed KohakuTerrarium.

### Installed with pip / pipx / uv

`pipx install kohakuterrarium` is the recommended way to use KohakuTerrarium from
a terminal — it puts all three commands on your PATH automatically. A plain
`pip install` into a virtual environment also creates them, but only inside that
environment's `bin/`.

If your environment's `bin/` isn't on your PATH, `kt shims` can link the commands
into a user directory:

```bash
kt shims status      # show where the commands are and whether they're on PATH
kt shims install     # symlink kt / kt-cli / kt-tui into ~/.local/bin
kt shims uninstall   # remove the shims it created
```

`kt shims` never edits your shell profile. If the target directory isn't on your
PATH, it prints the exact line to add. On Windows, prefer `pipx install
kohakuterrarium`, which manages PATH for you.

### Installed as the desktop app

The desktop app is GUI-first, but it can also hand you working terminal commands
even when you have no separate Python toolchain. From the app (or by running its
launch binary once), use:

```bash
kt shims install
```

Inside the desktop bundle this writes small wrapper scripts that forward to the
app's own bundled runtime, so `kt`, `kt-cli`, and `kt-tui` work in your terminal
without a separate install.

`kt doctor` reports your shim status alongside its other checks.
