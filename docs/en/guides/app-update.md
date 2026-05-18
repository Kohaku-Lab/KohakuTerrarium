---
title: App update
summary: How the KohakuTerrarium desktop app updates itself — the thin-wrapper Briefcase bundle, the managed venv, and the source / update-mode settings.
tags:
  - guides
  - update
  - briefcase
  - desktop
---

# App update

KohakuTerrarium's desktop app is a **thin wrapper around a managed
Python venv**.  The wrapper rarely changes; the framework inside it
updates via `pip` on a schedule you control.  No re-downloading
the installer for every release.

This guide covers what the wrapper does, where it stores its state,
how to choose where the framework comes from, and how to update / roll
back / recover if something goes wrong.

## The mental model

```
┌──────────────────────────────────────────────────────┐
│  Briefcase desktop bundle                            │
│  ┌────────────────────────────────────────────────┐  │
│  │  Wrapper (kohakuterrarium-launcher)            │  │
│  │  - Python runtime                              │  │
│  │  - bootloader (~/.kohakuterrarium/runtime/...) │  │
│  │  - splash UI                                   │  │
│  │  - bundled fallback wheels                     │  │
│  └────────────────────────────────────────────────┘  │
│                       │                              │
│                       ▼                              │
│  ┌────────────────────────────────────────────────┐  │
│  │  Managed venv (created on first launch)        │  │
│  │  ~/.kohakuterrarium/runtime/venv/              │  │
│  │  └── kohakuterrarium == <your chosen source>   │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

When you double-click the app:

1. The wrapper reads `~/.kohakuterrarium/app-settings.json`.
2. If the venv at `~/.kohakuterrarium/runtime/venv/` doesn't exist,
   the splash opens and the wrapper installs the framework from your
   configured source.
3. If the venv exists, the wrapper hands off (`exec`'s) to the venv's
   `kt` entry point — from this moment on you're running the framework
   directly, not the wrapper.

## Where things live

| Path | What |
|---|---|
| `~/.kohakuterrarium/app-settings.json` | source + update-mode settings (this file is what the **Admin → Updates** tab reads/writes) |
| `~/.kohakuterrarium/runtime/venv/` | the active managed venv |
| `~/.kohakuterrarium/runtime/venv.old/` | the previous venv, kept for one-shot rollback after each update |
| `~/.kohakuterrarium/runtime/.update.lock` | flock so two concurrent app launches don't race the same `pip install` |
| `~/.kohakuterrarium/logs/launcher.log` | rotating launcher log (1 MB × 3) |

## Choosing a source

The wrapper supports four source kinds.  Pick from **Settings → Updates → Source**:

| Source | What pip install runs | When to use |
|---|---|---|
| **PyPI stable** | `pip install -U kohakuterrarium` | Default for everyone. Latest released version. |
| **PyPI version pin** | `pip install -U kohakuterrarium==1.5.0` (or `<2.0`, etc.) | Locking to a specific version while testing or before rolling out a fleet. |
| **Git ref** | `pip install -U "git+<url>@main"` (branch/tag/commit) | Following a development branch, your own fork, or a release candidate not yet on PyPI. |
| **Local editable path** | `pip install -e /path/to/checkout` | Developers running from a Git clone. **Disables auto-update** — you're driving via `git pull`. |
| **Bundled (offline)** | `pip install --no-index --find-links=wheels-bundle/ kohakuterrarium` | First launch on an offline machine, or recovery when remote sources are unreachable. |

## Update mode

| Mode | What the wrapper does on launch |
|---|---|
| **Manual** | Never checks. You drive updates by clicking "Check now" / "Update" in the tab. |
| **Notify on launch** *(default)* | Checks PyPI / git in the background (24-hour cache). If newer, a banner appears in Settings → Updates. You click "Update" to install. |
| **Auto on launch** | Checks AND installs on launch (splash shows progress) if a newer version is available. You can cancel; cancel falls back to the existing venv. |

`source.kind=local` overrides every mode to **Manual** — editable installs are
user-managed.

## The update flow, in detail

When you click **Update** (or the wrapper triggers an auto-update):

1. **Flock** `~/.kohakuterrarium/runtime/.update.lock` so a second
   launch can't race.
2. Build a fresh venv at `~/.kohakuterrarium/runtime/venv.new/`.
3. Run `pip install` into it per your source.
4. **Smoke-test**: import the framework, run `kt --help`. Both must
   exit cleanly within 30 seconds.
5. If smoke passes, rename `venv → venv.old` and `venv.new → venv`
   atomically.  These are kernel-level renames (instant; no copy).
6. Persist the new version + check timestamp to `app-settings.json`.
7. Release the flock.  Restart the app to pick up the new version.

If anything in steps 3-5 fails, the wrapper deletes `venv.new/` and
leaves the existing `venv/` untouched.  You see the error in the
progress modal; your app keeps working on the version it was already
running.

## Rollback

After every successful update, the previous venv is kept at
`~/.kohakuterrarium/runtime/venv.old/`.  Click **Rollback** in the
Updates tab to swap it back into place.  Only one rollback is
available (the next successful update overwrites `venv.old`).

## Recovery — when both venvs are broken

If `venv/` and `venv.old/` are both gone or unusable, the wrapper
falls back to the **bundled wheels** shipped inside the Briefcase
artifact.  The Updates tab surfaces a "Recovery mode" banner with a
**Reset venv from bundled wheels** button.  This reinstalls the
framework from the offline copy, restoring a working app even when
your network or chosen source is unreachable.

## Offline first launch — bundled-first install

The desktop bundle (MSI / `.app` / AppImage) **ships the framework as
a directory of wheels** alongside the launcher.  On a fresh install
the wrapper installs from those bundled wheels instead of reaching
out to PyPI, so the first launch always works — even with no internet
or behind a firewall.

The rule the wrapper follows:

| Scenario | First-install pip call |
|---|---|
| Default settings + bundled wheels present in the artifact | `pip install --no-index --find-links=<bundled>/ kohakuterrarium` |
| User changed `source.kind` from the default (e.g. set Git) | Honour the user's choice — bundled wheels are ignored |
| Bundled wheels missing (dev install, broken bundle) | Fall through to `pip install kohakuterrarium` per the configured source |
| Bundled install fails (corrupt wheel) + default source | Auto-recover by trying PyPI |
| Bundled install fails + user picked Git/local | Surface the error — don't paper over the user's intent |

After the first install completes, the **Installed** line in the
Updates tab reads `Installed: 1.5.x (from bundled offline copy)` so
you can tell at a glance which source produced the running venv.

### Updates remain a PyPI fetch by default

The bundled-first behaviour applies only to the **initial install**.
Subsequent updates (manual, notify-on-launch, auto-on-launch) honour
`source.kind` — which defaults to PyPI.  When you click **Update**,
the wrapper fetches the latest from PyPI as usual; the bundled wheels
are untouched and remain the C2 fallback.

The Update button label reflects what the click will do:

- Source = PyPI → `Update to <X> from PyPI`
- Source = Git → `Update from git`
- Source = Local → `Reinstall editable`
- Source = Bundled (explicit) → `Reinstall from bundled (same version)`

If you want to **stay on the bundled version forever**, set
**Update mode → Manual** and never click Update.  The wrapper will
never reach the network.

## CLI parity: `kt self-update`

The same flow is available from the terminal:

```bash
kt self-update                  # update via configured source
kt self-update --dry-run        # print what would run; don't change anything
kt self-update --check-only     # exit 0 if newer available, 1 if up-to-date
kt self-update --source git --spec "https://github.com/.../@main"
```

`kt self-update` auto-detects how KohakuTerrarium was installed and
picks the right protocol:

- **Wrapper-managed venv** → runs the atomic-rename flow (same as the GUI).
- **pipx** → `pipx upgrade kohakuterrarium`.
- **Editable install** → refuses; tells you to `git pull` in your checkout.
- **System package** (`/usr/bin/python`) → refuses; tells you to use the
  platform package manager.
- **Other user venv** → runs `pip install -U` in the current interpreter.
  Atomic rename + rollback are wrapper-only and not available here.

## Migrating from the legacy bundle

KohakuTerrarium 1.5.0 ships both the old "frozen full framework"
Briefcase bundle AND the new wrapper bundle.  If you're on the
legacy bundle, Settings → Updates will show a one-shot
"Switch to the new auto-updating bundle" banner with a link to the
release page.  You download the wrapper installer **once**, run it,
and from then on every update is a pip operation in the wrapper's
venv.  No more installer downloads.

The wrapper preserves your `~/.kohakuterrarium/` user data (sessions,
profiles, MCP servers, API keys).  Nothing in your config moves; only
the framework source code is re-installed into a fresh venv.

## Troubleshooting

- **First launch hangs at "Installing framework"** — check
  `~/.kohakuterrarium/logs/launcher.log` for the pip output. Most
  often it's a network / proxy / firewall issue. Try switching the
  source to **Bundled (offline)** in the tab to install from the
  fallback wheels.
- **"Another update is in progress"** — a previous update crashed and
  left the lockfile behind. After 10 minutes the wrapper offers an
  "Override stale lock" prompt; agree and retry.
- **Smoke test fails after install** — the install completed but `kt
  --help` won't run. Click **Rollback** to swap back to `venv.old/`.
  If that's also broken, click **Reset venv from bundled wheels** to
  restore the offline copy.
- **Editable install but `kt self-update` refuses** — that's
  intentional. Update your checkout with `git pull`, then re-run
  `pip install -e .` to refresh installed metadata.

## See also

- [Deployment — Docker](deployment-docker.md) — the container update flow uses `docker pull` instead.
- [Deployment — systemd](deployment-systemd.md) — for systemd hosts, run `kt self-update` and then `systemctl restart kohakuterrarium-host` to pick up the new version.
- [Serving](serving.md) — `kt serve` is what the framework's `kt` entry runs after the wrapper hands off.
