# Terminal install

The one-line installers and what they download.

| File | What |
|---|---|
| `install.sh` | Linux / macOS: `curl -fsSL .../manifests-stable/install.sh \| sh` |
| `install.ps1` | Windows: `irm .../manifests-stable/install.ps1 \| iex` |
| `build_runtime.py` | Builds `kt-runtime-<platform>.tar.gz` (`.zip` on Windows) + `.sha256`: the pinned Python (`../desktop/python-runtime.json`) and the launcher package, staged with the desktop app's helpers (`../desktop/stage_runtime.py`) |

## How an install goes

1. The installer downloads `kt-runtime-<platform>` from the channel's fixed release
   (`manifests-<channel>`), checks its sha256, and unpacks it into
   `~/.kohakuterrarium/runtime/terminal/` (`python/`, `bootstrap/`).
2. It runs `python -m kohakuterrarium.launcher --install --channel <channel>`: the launcher
   reads the channel manifest, downloads the release tree for the platform, checks and
   activates it in `~/.kohakuterrarium/runtime/versions/` (shared with the desktop app),
   with progress on stderr (`KT_LAUNCHER_TERMINAL=1`).
3. It runs `kt shims install` through the launcher, with `KT_PYTHON` / `KT_BOOTSTRAP_DIR`
   naming the runtime, which writes `kt`, `kt-ui`, `kt-cli`, `kt-tui` wrappers into
   `~/.local/bin` (Windows: `%LOCALAPPDATA%\KohakuTerrarium\bin`, added to the user PATH).

Afterwards `kt self-update` installs newer trees (and refreshes the launcher copy in
`runtime/terminal/bootstrap`); `kt self-uninstall` removes the wrappers and the runtime.

## Releases

`.github/workflows/release.yml` builds `kt-runtime-*` on each platform (job
`build-terminal-runtime`) and uploads them with both installers to `manifests-<channel>`,
next to the channel manifest, overwriting the previous ones. Release trees include the
`ktui` extra.

## Testing an installer locally

Both installers take a base URL for the runtime and a feed URL (`KT_INSTALL_BASE`,
`KT_FEED_URL`; `install.sh` also `--base`, `--feed-url`). The launcher accepts plain
`http://` for loopback hosts, so a folder served with `python -m http.server` holding a
runtime archive, release trees and a manifest from `scripts/publish_manifest.py` stands in
for a release. Use a temporary `KT_CONFIG_DIR` / `HOME` (and on Windows
`KT_NO_MODIFY_PATH=1`).
