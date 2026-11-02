# Desktop source development

Run the real Tauri window against the editable Python checkout and Vite, without
building a release archive or installer for each change.

## Prepare once

Install uv, Node.js/npm, Rust through rustup, and the platform's
[Tauri prerequisites](https://v2.tauri.app/start/prerequisites/). Windows needs
C++ build tools and WebView2; macOS needs Xcode command-line tools. Use Python
3.12 or newer: CI tests 3.12+, although the package itself allows 3.10.

From the repository root:

```sh
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
npm ci --prefix src/kohakuterrarium-frontend
```

Reopen the terminal after installing Rust so `cargo` is on PATH. The Tauri CLI is
pinned in the frontend dependencies. The first launch downloads crates and
compiles Rust; no staged Python distribution or production SPA is required.

## Daily workflow

```sh
python scripts/dev_desktop.py --check
python scripts/dev_desktop.py
```

The runner locates the repository from its own path, uses the checkout's `.venv`
by default, and verifies that this Python imports the checkout. Relative
`--python` and `--data-dir` values are resolved from the current directory.

| Edit | Verify in the desktop window |
| --- | --- |
| Vue / JavaScript / CSS | Save; Vite applies HMR or reloads when needed |
| Python | Save, then **Restart backend** in Settings → Desktop app or the tray |
| Rust | Save; Tauri recompiles and relaunches |
| Dependencies | Install into the selected Python/frontend environment, then restart |

Python does not reload automatically. A backend restart can interrupt active work
and may lose unsaved input. Closing the window exits; with close-to-tray enabled,
use **Quit** in the tray or Ctrl+C in the terminal.

Optional overrides:

```sh
python scripts/dev_desktop.py --python /absolute/path/to/python
python scripts/dev_desktop.py --data-dir "./development data"
python scripts/dev_desktop.py --frontend-port 15174 --backend-port 18002
python scripts/dev_desktop.py --no-watch
```

## Boundaries

- Vite and Python bind to `127.0.0.1`, on ports `15173` and `18001` by default.
  A busy port fails the start; the runner never attaches to or stops its owner.
- Python data defaults to `.internal/desktop-dev`. A separate application
  identifier isolates shell settings and WebView storage from the installed app;
  credentials are not copied over.
- Runtime repair, packaged updates, autostart, Explorer registration, terminal
  command installation and system-keychain management are disabled both in the UI
  and at their native/API entry points. Keys are stored in a file. Provider
  environment variables still apply, so configured providers make real API calls.
- Source mode needs the `source-dev` Cargo feature, which release builds reject.
  Builds without the feature ignore the development configuration and use the
  bundled runtime.
- Out of scope: remote Vite or backends, several instances sharing one data
  folder, and keeping running tasks across a restart. Installer, first
  extraction, update and OS-registration behavior still need a packaged build.

## How it works

The runner checks prerequisites and hands one configuration to the official
`tauri dev`. Tauri starts Vite and watches Rust; the existing native backend
manager starts, restarts and stops the Python child. Vite and the shell both exit
when the runner process disappears, so a failed CLI cannot leave them running.

Vite forwards `/api`, `/ws/` and the launch handshake to Python without rewriting
Host or Origin, so the one-time token, the HttpOnly session cookie and the origin
checks stay in force. After each backend start, the main page must complete an
authenticated `/api/settings/ui-prefs` request for the current start generation
before session windows reopen; a redirect alone does not count.

## Checks

```sh
pytest tests/unit/packaging/test_dev_desktop.py tests/unit/serving/desktop/test_server.py
pytest tests/integration/test_serving.py
npm run test:desktop-dev --prefix src/kohakuterrarium-frontend
cd packaging/desktop/src-tauri
cargo test --features source-dev --locked
cargo test --locked
```

The `KT_DESKTOP_SMOKE_OUT` self-check described in [README.md](README.md) also
runs in source mode with `KT_DESKTOP_SMOKE_BACKEND`, `KT_DESKTOP_SMOKE_POPOUT`
and `KT_DESKTOP_SMOKE_OVERLAY`. Installer, update and repair probes require a
packaged app.
