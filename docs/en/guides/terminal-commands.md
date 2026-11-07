---
title: Terminal commands (kt-ui, kt-cli, kt-tui)
summary: Install KohakuTerrarium for the terminal with one line, work with agents in kt-ui, keep sessions running in the background, and manage the install.
tags:
  - guides
  - cli
  - tui
  - install
---

# Terminal commands

In a terminal you use two commands:

- **`kt-ui`** to work with agents: ktui, KohakuTerrarium's terminal front end.
- **`kt`** to manage things: config, login, packages, `kt serve`, updates.

`kt-cli` and `kt-tui` are `kt-ui` opening in a given layout (see below); `kt ui`, `kt cli`
and `kt tui` are the same commands as subcommands of `kt`.

## Install with one line

Linux and macOS:

```sh
curl -fsSL https://github.com/Kohaku-Lab/KohakuTerrarium/releases/download/manifests-stable/install.sh | sh
```

Windows PowerShell:

```powershell
irm https://github.com/Kohaku-Lab/KohakuTerrarium/releases/download/manifests-stable/install.ps1 | iex
```

It installs for your user only, without administrator rights, and needs no Python of your
own:

| What | Where |
|---|---|
| A Python and the launcher, for the terminal | `~/.kohakuterrarium/runtime/terminal/` |
| The release itself (the desktop app uses the same trees) | `~/.kohakuterrarium/runtime/versions/` |
| `kt`, `kt-ui`, `kt-cli`, `kt-tui` | `~/.local/bin/` (Windows: `%LOCALAPPDATA%\KohakuTerrarium\bin\`) |

On Windows the installer adds the commands' folder to your user PATH. On Linux and macOS
`~/.local/bin` is usually on PATH already; when it is not, the installer prints the line to
add to your shell's startup file.

Options:

| | Linux / macOS (`... \| sh -s -- ...`) | Windows (set before `irm`) |
|---|---|---|
| Release channel (`stable`, `beta`, `nightly`) | `--channel nightly` | `$env:KT_CHANNEL = "nightly"` |
| Leave PATH alone | (never edited) | `$env:KT_NO_MODIFY_PATH = "1"` |
| Another home than `~/.kohakuterrarium` | `KT_CONFIG_DIR=...` | `$env:KT_CONFIG_DIR = "..."` |

Builds exist for Linux (x64, arm64), macOS on Apple silicon and Windows x64. Elsewhere,
install with pip (below).

### Update and uninstall

```sh
kt self-update             # the newest release of your channel, installed beside the current one
kt self-update --rollback  # back to the previous one
kt self-uninstall          # remove the commands and the runtime; config and sessions stay
kt self-uninstall --purge  # remove everything in ~/.kohakuterrarium
```

`kt-ui` says so when a newer release is out. Updates install side by side and switch over
at once.

### With pip, pipx or uv instead

```sh
pipx install "kohakuterrarium[ktui]"     # or: uv tool install "kohakuterrarium[ktui]"
```

The `ktui` extra brings kohakutui, which `kt-ui` needs; without it `kt-cli` and `kt-tui` run
the previous front ends. `kt self-update` is for installs the launcher manages: update a pip
install with pip.

## `kt-ui`

```sh
kt-ui                                         # the launcher
kt-ui @kt-biome/creatures/general             # start a creature (or a terrarium)
kt-ui general --add critic --channel reviews  # an ad-hoc team
kt-ui resume [query | --last]                 # a saved session
kt-ui attach [session]                        # a session running in the KT server
```

The launcher lists, in one picker:

- **running**: sessions in the background KT server; pick one to attach to it or stop it.
- **recent**: saved sessions (type to search); resume, rename or delete one.
- each package's **creatures and terrariums**, to start.

`--inline`, `--hybrid` and `--fullscreen` pick the layout for one run; otherwise the
remembered one is used (change it with `/renderer` or in Settings). The other options
(`--llm`, `--session` / `--no-session`, `--log-level`, `--log-stderr`) match `kt run`.

### Sessions that outlive the terminal

```sh
kt-ui --daemon @kt-biome/creatures/general
```

runs the session in the background KT server instead of in the terminal. `/exit` then leaves
it working; `kt-ui attach` (or the launcher's running group) comes back to it from any
terminal, several at once, or from the web UI. `/stop` in an attached session stops it there;
it stays saved and can be resumed.

The background server is the one `kt serve` runs. When none is running, `--daemon` starts
it, and a server started that way stops by itself two minutes after its last session has
ended. A server older than your install (updated since it started) is restarted when it has
no sessions. Attached to a server on this machine, the Debug page's Logs view shows its log
too.

### First run

With no model set up yet, `kt-ui` asks before your first message: sign in with Codex (a
ChatGPT subscription), add a provider with an API key, or pick a model. Settings (alt+5) and
`/login` do the same any time.

## `kt-cli` and `kt-tui`

- **`kt-cli`** opens `kt-ui` inline, in your terminal's scrollback (or hybrid when you have
  chosen that layout; it does not open full screen).
- **`kt-tui`** opens `kt-ui` full screen.

They take the same arguments as `kt-ui`. The previous front ends, the rich prompt-toolkit CLI
and the Textual TUI, remain as **`legacy-cli`** and **`legacy-tui`** for one release, and
are what `kt-cli` / `kt-tui` run when kohakutui is not installed.

## Commands on PATH for other installs: `kt shims`

```bash
kt shims status      # where the commands are and whether they are on PATH
kt shims install     # kt / kt-ui / kt-cli / kt-tui into ~/.local/bin
kt shims uninstall   # remove the ones it created
```

With pip in a virtual environment the commands live in that environment's `bin/`; `kt shims
install` links them into a user directory. It never edits your shell profile: when the
directory is not on PATH it prints the line to add. On Windows, prefer pipx, which manages
PATH for you.

### Installed as the desktop app

Turn on **Settings → Preferences → Desktop app → Terminal command**. The switch writes the
wrappers into a per-user directory and adds it to your PATH (the per-user registry value on
Windows, a marked block in `~/.zprofile` on macOS), without administrator rights; turn it
off to remove both. The wrappers run the app's bundled interpreter and release, so the
commands follow the app's updates. `kt shims install` / `uninstall` do the same without
touching PATH.

`kt doctor` reports your shim status alongside its other checks.
