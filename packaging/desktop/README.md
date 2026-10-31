# Desktop shell (Tauri 2)

A native window around the local Python backend, shipped as `.msi`/`.exe` on
Windows and `.dmg`/`.app` on macOS. `kt app` (pywebview) shares the same page,
server and host-bridge contract.

```
Tauri shell ── spawns ──> python/python(.exe) -m kohakuterrarium.launcher.sidecar
   │                         │ prepares the versioned release (first run: bundled-release/)
   │ progress events         │ runs serving/desktop/server.py
   ▼                         ▼
 shell/index.html        http://127.0.0.1:<port>/?kt_launch=<token>  (cookie-gated)
```

## Layout

| Path | Purpose |
|---|---|
| `src-tauri/src/backend.rs` | start, watch and stop the Python backend; progress and state-file protocol |
| `src-tauri/src/window.rs` | main window, navigation policy, tray |
| `src-tauri/src/commands.rs` | native operations the app page may call (mirrors the pywebview shell) |
| `src-tauri/src/navigation.rs` | which URLs may load in the window |
| `src-tauri/src/smoke.rs` | headless self-check for installer tests |
| `src-tauri/capabilities/main.json` | the loopback origin and the commands it may invoke |
| `shell/index.html` | loading and error page shown before the backend is ready |
| `stage_runtime.py` | downloads the pinned interpreter, stages the launcher and the release archive |
| `python-runtime.json` | pinned python-build-standalone release |

The Rust constants and names that Python and the frontend also use are compared by
`tests/unit/packaging/test_desktop_contract.py`.

## Build

```
python packaging/desktop/stage_runtime.py --platform win-x64 --release-dir <dir with kohakuterrarium-*.tar.zst>
cd packaging/desktop/src-tauri
cargo tauri build        # or: cargo build  (development)
```

`--stamp-version` writes the project's numeric core version into `tauri.conf.json`.

## Run without installing

```
KT_DESKTOP_RESOURCES=<stage dir> cargo run
```

`KT_DESKTOP_SMOKE_OUT=<file>` runs headless: the window stays hidden, and once the
backend is ready and the page has loaded the shell writes a JSON report there and
exits (status 0 when the page loaded).

With `KT_DESKTOP_SMOKE_OVERLAY` also set, the shell opens a native overlay window
(`overlay.rs`) and the report's `overlayWindow` says whether that window loaded the
app page and called a native command from it. `KT_DESKTOP_SMOKE_TRAY` creates the
tray icon and reports whether its attention badge can be shown and cleared
(`tray`). `KT_DESKTOP_SMOKE_SUMMON` registers and releases a system-wide shortcut
(`summonShortcut`). `KT_DESKTOP_SMOKE_POPOUT` opens a session window and reports
whether it loaded the app page and called a native command (`popoutWindow`). The
installer tests always set all four plus `KT_DESKTOP_SMOKE_MENU`, which builds the
application menu and counts its top-level menus (`menu`).

With `KT_DESKTOP_SMOKE_UPDATE` also set, the self-check fetches the update feed and
downloads the advertised build, which verifies its signature; the report's
`shellUpdate` says what happened. To try it locally: build a signed installer
(`TAURI_SIGNING_PRIVATE_KEY=<key> tauri build --bundles nsis --config
tauri.updater.conf.json --config <{"version": "9.9.9"}>`), publish it with
`updater_manifest.py` on a local HTTP server, and build the shell with an override
config that points `plugins.updater.endpoints` there and sets
`dangerousInsecureTransportProtocol`.

## Updates

The shell updates itself from `latest.json` on the latest stable release, signed
with the key whose public half is in `tauri.conf.json`. The private half is the
`TAURI_SIGNING_PRIVATE_KEY` repository secret; builds without it produce unsigned
installers and no updater artifacts. The Python runtime updates separately through
the launcher.

## Signing

Builds are unsigned for Windows and ad-hoc signed for macOS. Signing steps in CI run
only when certificate secrets are present.
