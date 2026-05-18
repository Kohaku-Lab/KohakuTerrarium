---
title: App update
summary: How the KohakuTerrarium desktop app updates itself — release-bundle download, side-by-side versioned installs, atomic pointer swap, custom mirrors, channels.
tags:
  - guides
  - update
  - briefcase
  - desktop
---

# App update

The KohakuTerrarium desktop app updates itself by **downloading a
pre-built release tarball** for your platform + Python ABI, extracting
it side-by-side with the current install, smoke-testing it, and
atomically flipping a small pointer file to switch which version
launches next. The model is borrowed from native-app updaters like
Squirrel / Velopack / Sparkle — small, transactional, and one
HTTPS GET + one extract per update.

Crucially, your machine does **not** run `pip`, `venv`, `git`, or
`ensurepip` to update. Those exist on the build machine; you receive
the result.

## The mental model

```
┌──────────────────────────────────────────────────────────────┐
│  Briefcase desktop bundle                                    │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Launcher (~50KB Python, urllib + hashlib + tarfile)   │  │
│  │  - reads runtime/active                                │  │
│  │  - downloads + extracts release tarballs               │  │
│  │  - exec's into versions/<active>/scripts/kt            │  │
│  └────────────────────────────────────────────────────────┘  │
│  + bundled-release/kohakuterrarium-<v>-<plat>-py<X.Y>.tar.zst│  ← offline first-launch
└──────────────────────────────────────────────────────────────┘

User home (~/.kohakuterrarium/):
├── app-settings.json
└── runtime/
    ├── active                      ← pointer JSON
    ├── versions/
    │   ├── 1.5.0/                  ← extracted release tree
    │   │   ├── site-packages/
    │   │   ├── scripts/kt
    │   │   └── manifest.json
    │   ├── 1.5.1/
    │   └── 1.5.2-nightly-2026-05-19/
    └── manifest-cache/
        ├── stable.json
        ├── beta.json
        └── nightly.json
```

Each version lives in its own directory. Switching versions means
rewriting the 50-byte `active` pointer (atomic on POSIX and Windows).
The currently-running process is unaffected — the new version takes
effect on next launch.

## First launch

If the bundle was shipped with `bundled-release/<tarball>`, first
launch extracts that offline tarball into `versions/<v>/` and points
`active` at it. You can then update to a newer version from the GUI
or CLI when network is available.

Without a bundled tarball (developer install / minimal bundle), first
launch resolves the configured channel manifest over the network,
downloads + verifies + extracts the matching artifact, and writes the
pointer. If no network is available either, the launcher surfaces a
"network required for first launch" error rather than silently bricking.

## Updating

| Surface | How |
|---|---|
| Admin → Updates tab in the desktop app | Settings + "Check now" + "Update" buttons |
| `kt self-update` | CLI parity |
| `kt self-update --check-only` | Resolve + print latest; exit 0 if newer, 1 if up-to-date |
| `kt self-update --dry-run` | Resolve + print what would be installed |
| `kt self-update --rollback` | Revert pointer to the previous installed version |

Updates do not modify the running process. After an update succeeds,
quit and relaunch the app — the new pointer is read on next launch.

## Channels

| Channel | What | When you'd pick it |
|---|---|---|
| `stable` | Tested releases | default, recommended |
| `beta` | Pre-release candidates | helping us validate the next major |
| `nightly` | Daily automatic builds | cutting-edge / contributors |

The channel selector lives in **Admin → Updates → Channel** and in
`kt self-update --channel <name>` (sticky — written back to settings).

## Pinned version

Pin to a specific version to ignore channel updates until you unpin.
Useful when:

- A new release regresses behaviour you depend on and you want time
  to report + wait for a fix.
- A managed deployment standardises on a known version across nodes.

`kt self-update --pin 1.5.0` sets it; `--pin ""` clears it. The GUI
exposes a dropdown populated from the channel manifest.

## Custom feeds (mirrors / offline servers)

Corporate / air-gapped users can host the release tarballs on their
own HTTPS server. The launcher needs:

- A `<channel>.json` manifest at `<your-base-url>/<channel>.json`
  (schema documented below).
- The tarball URLs inside the manifest pointing at wherever you've
  staged the artifacts (your mirror, intranet, S3-like store…).

Switch to a custom feed via **Admin → Updates → Release feed →
Custom mirror**, paste the base URL, or via CLI:

```
kt self-update --feed-url https://internal.mirror/kt --channel stable
```

The launcher fetches `<base>/<channel>.json` exactly as written, then
downloads + verifies the artifact whose `(platform, py_abi)` matches.
Custom feeds support exactly the same channel + pinning semantics as
the default GitHub Releases feed.

### Channel manifest schema

```json
{
  "schema": 1,
  "channel": "stable",
  "generated_at": "2026-05-19T00:00:00Z",
  "releases": [
    {
      "version": "1.5.1",
      "build_id": "20260519-153000-abc1234",
      "release_notes_url": "https://your.mirror/notes/1.5.1.md",
      "artifacts": [
        {
          "platform": "linux-x64",
          "py_abi": "cp313",
          "url": "https://your.mirror/dl/kohakuterrarium-1.5.1-linux-x64-py3.13.tar.zst",
          "sha256": "9f86d0...",
          "size_bytes": 178234567
        }
      ]
    }
  ]
}
```

Platform tags: `linux-x64`, `linux-arm64`, `macos-x64`, `macos-arm64`,
`win-x64`. ABI tags: `cp311`, `cp312`, `cp313`, `cp314`.

## Update modes

| Mode | Behaviour on launch |
|---|---|
| `manual` | Never check; you update from the UI explicitly |
| `notify-on-launch` | Check daily; show a banner if newer is available |
| `auto-on-launch` | Check + install before exec-ing the framework |

Set via **Admin → Updates → Update mode**.

## Rollback

Side-by-side installs keep prior versions on disk. Rollback rewrites
the pointer to the most-recent non-active version. No re-download.

```
kt self-update --rollback
```

Or click **Rollback to <prev>** in the Admin → Updates tab. GC keeps
the active + previous + `update.keep-versions` most-recent (default
3, so up to 5 versions on disk).

## Settings file

`~/.kohakuterrarium/app-settings.json`:

```json
{
  "feed": {
    "kind": "github_releases",
    "repo": "Kohaku-Lab/KohakuTerrarium",
    "url": null
  },
  "channel": "stable",
  "pinned_version": null,
  "update": {
    "mode": "notify-on-launch",
    "check-cache-hours": 24,
    "keep-versions": 3
  },
  "runtime": {
    "active-version": "1.5.1",
    "active-build-id": "20260519-153000-abc1234",
    "last-check-at": "2026-05-19T12:34:56Z",
    "last-check-error": null
  }
}
```

Hand-editing is fine; invalid fields fall back to defaults with a
one-line warning rather than wedging the launcher.

`--reset-settings` overwrites with defaults; `--reset-runtime` wipes
`runtime/versions/` and re-runs first install.

## What the launcher does NOT depend on

- `pip` — not bundled, not invoked
- `venv` / `ensurepip` — not used (the briefcase shell strips these
  on Windows anyway, which is why the previous design didn't work)
- `git` — not invoked
- PyPI — only the configured feed (github_releases or custom) is queried
- Any third-party HTTP client — `urllib` only

The only optional third-party dep is `zstandard` for `.tar.zst`
support. `.tar.gz` is the fallback path; everything works without
`zstandard` if your mirror serves `.tar.gz` artifacts.

## Developer note

If you run the framework from a git checkout (`pip install -e .` in
your own Python), the launcher is irrelevant. `kt self-update` will
detect you're outside a launcher install and refuse with a one-line
"use git pull" hint rather than try to manage your dev environment.

## Failure recovery

| Failure | What happens |
|---|---|
| Tarball sha256 mismatch | Partial dir removed, abort with "Download corrupted" |
| Smoke test fails on new version | Partial dir removed, active version untouched |
| Disk full mid-extract | Partial dir removed |
| Pointer file corrupted | Launcher scans `versions/` and recovers the newest valid one |
| Manifest URL returns 5xx | Use cached manifest if fresh (<24h) else surface error |
| Both `versions/` and bundled-release missing | "Network required" error, no silent brick |

## See also

- [Configuration reference](../reference/configuration.md) — every settings field
- [CLI reference](../reference/cli.md) — full `kt self-update` flags
