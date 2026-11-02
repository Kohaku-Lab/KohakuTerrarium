# Desktop source development

Run the real Tauri window against the editable Python checkout and Vite, without
building a release archive or installer for each change.

## Prepare once

Use Python 3.12+ for development (the package declares Python >=3.10; the
CI matrix tests 3.12+). Install uv, Node.js/npm, Rust through rustup, and the platform's
[Tauri prerequisites](https://v2.tauri.app/start/prerequisites/). Windows needs
C++ build tools and WebView2; macOS needs Xcode command-line tools.

From the repository root:

```sh
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
npm ci --prefix src/kohakuterrarium-frontend
```

Reopen the terminal after installing Rust so `cargo` is on PATH. The Tauri CLI is
pinned in the frontend dependencies. First launch still downloads dependencies
and compiles Rust; no staged Python distribution or production SPA is required.

## Daily workflow

```sh
python scripts/dev_desktop.py --check
python scripts/dev_desktop.py
```

The runner finds the repository independently of the working directory, defaults
to its `.venv`, and verifies that Python imports this checkout. Relative
`--python` and `--data-dir` paths are resolved from the current working directory.

| Edit | Verify in the desktop window |
| --- | --- |
| Vue / JavaScript / CSS | Save; Vite applies HMR or reloads when needed |
| Python | Save, then **Restart backend** in Settings → Desktop app or the tray |
| Rust | Save; Tauri recompiles and relaunches |
| Dependencies | Install into the selected Python/frontend environment, then restart |

Python does not automatically reload. Backend restart can interrupt active work
and does not guarantee preservation of unsaved input. Close normally to exit;
with close-to-tray enabled, use **Quit** in the tray or Ctrl+C in the terminal.

Optional overrides:

```sh
python scripts/dev_desktop.py --python /absolute/path/to/python
python scripts/dev_desktop.py --data-dir "./development data"
python scripts/dev_desktop.py --frontend-port 15174 --backend-port 18002
python scripts/dev_desktop.py --no-watch
```

## Boundaries

- Vite and Python bind to `127.0.0.1`, on ports `15173` and `18001` by default.
  Busy ports fail; nothing attaches to or terminates their existing owner.
- Python data defaults to `.internal/desktop-dev`. Use a dedicated development
  folder. A separate application identifier isolates shell settings and WebView
  storage from the installed app; credentials are not copied automatically.
- Runtime repair, packaged updates, autostart, Explorer registration, terminal
  installation and system-keychain management are disabled at their entry points
  as well as in the UI. Development keys use file storage. Provider environment
  variables still apply, and configured providers can make real API calls.
- Source mode requires the explicit `source-dev` Cargo feature and validated
  configuration. Release builds reject that feature; ordinary builds ignore the
  development environment variable and keep using the bundled runtime.
- Remote Vite/backends, simultaneous instances sharing a data folder and seamless
  migration of running tasks are outside scope. Installer, extraction, update and
  OS-registration behavior still require packaged validation.

## Implementation and checks

The Python runner delegates to the official Tauri CLI. Its Vite hook serves the
frontend; the existing native Backend manager owns the Python child, ready-state
file, restart and shutdown. Both Vite and the source shell follow the runner's
lifetime to cover CLI failure paths that would otherwise leave services behind.

Vite forwards `/api`, `/ws/` and the dedicated launch handshake to Python without
rewriting Host/Origin. The existing one-time token and HttpOnly cookie gate remains
enabled. On restart, session windows wait for the main page's authenticated API
confirmation for the current generation. The protected `/api/settings/ui-prefs`
request verifies that the WebView sends a valid session cookie; a completed
redirect or a successful `/healthz` request does not establish authentication.

The bundled shell first loads the backend's `/healthz` document, then starts the
token exchange from that exact origin. This keeps macOS WebView navigation from
dropping the Strict cookie on a cross-site redirect from the embedded loading
page. The bootstrap document is not counted as application readiness. Source mode
already starts on the Vite origin and keeps its existing handshake.

```sh
pytest tests/unit/packaging/test_dev_desktop.py tests/unit/serving/desktop/test_server.py
pytest tests/integration/test_serving.py
npm run test:desktop-dev --prefix src/kohakuterrarium-frontend
cd packaging/desktop/src-tauri
cargo test --features source-dev --locked
cargo test --locked
```

The existing `KT_DESKTOP_SMOKE_OUT` harness supports source mode, including
`KT_DESKTOP_SMOKE_BACKEND`, `KT_DESKTOP_SMOKE_POPOUT` and
`KT_DESKTOP_SMOKE_OVERLAY`. The main-window smoke acknowledgement requires a mounted Vue application and a
successful authenticated request from the WebView. Installer/update/repair probes
require a packaged app.
