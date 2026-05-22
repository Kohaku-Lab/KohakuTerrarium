# Android packaging

The Android APK ships:

- Python 3.13 + the framework wheel + its deps (via Briefcase Android)
- The Vue web frontend (`src/kohakuterrarium/web_dist/`)
- A bundled sandbox of static Linux binaries — **not committed**;
  fetched by CI at build time
- A foreground service that boots `kt serve` on loopback; a WebView
  loads the frontend pointed at the embedded host

This directory holds the packaging metadata + helper scripts.  The
generated APK / AAB / Briefcase build outputs live under
`build/kohakuterrarium/android/` (gitignored).

## Files

| File | Purpose |
|---|---|
| `sandbox_manifest.toml` | Pinned versions + SHA256 of every static binary, per ABI.  Source of truth for the bundled sandbox. |
| `fetch_sandbox.py` | CI script: downloads each artifact, verifies SHA256, extracts (tarballs) or copies (direct), lays out `bin/<abi>/<name>`.  Emits `bin/manifest.json` for the runtime extraction helper. |
| `bin/` (gitignored) | CI-populated.  Do not commit. |
| `template/` (future) | Customised Briefcase Android template adding the WebView MainActivity + foreground service.  Not present yet — Phase B work. |

## Bumping a binary version

1. Edit `sandbox_manifest.toml` — change the version + URL.
2. Run the fetcher in **refresh** mode locally:

   ```bash
   python packaging/android/fetch_sandbox.py --refresh
   ```

   It downloads the new artifacts and prints their actual SHA256
   hashes.  Paste those into `sandbox_manifest.toml` (replacing
   the `REPLACE_ME_*` or previous hash values).

3. Run again in normal mode to verify everything passes:

   ```bash
   python packaging/android/fetch_sandbox.py
   ```

4. Commit only `sandbox_manifest.toml`.  CI re-fetches on the
   next build.

## Building the APK locally

Requires:

- Python 3.13
- JDK 17 (Briefcase Android currently breaks on newer JDKs)
- Android SDK + NDK (Briefcase will offer to download if missing)

```bash
# Once, per repo clone:
python -m pip install briefcase

# Fetch + verify sandbox (~6MB total, cached after first run)
python packaging/android/fetch_sandbox.py

# Build the frontend (once or when frontend changes)
( cd src/kohakuterrarium-frontend && npm ci && npm run build )

# Build the APK.  ``postcreate.py`` between create + update is
# mandatory — Briefcase has no native way to merge our custom
# Java into the generated tree; that script does the merge.
briefcase create android
python packaging/android/postcreate.py
briefcase update android
briefcase build android

# Output:
ls build/kohakuterrarium/android/gradle/app/build/outputs/apk/
```

For a signed AAB ready for Play Store:

```bash
briefcase package android \
    --keystore /path/to/release.jks \
    --keystore-pass "${ANDROID_KEYSTORE_PASSWORD}" \
    --key-alias "${ANDROID_KEY_ALIAS}" \
    --key-pass "${ANDROID_KEY_PASSWORD}"
```

## CI

`.github/workflows/android.yml` runs the full build on tag push +
on workflow_dispatch.  Sandbox-fetch results are cached between
runs keyed by the manifest hash, so version-bump triggers a fresh
fetch and a stable manifest is a cache hit.

A separate fast `validate-manifest` job runs the fetcher's unit
tests on every PR to catch schema regressions without waiting for
the full Briefcase build.

## Python dependency posture for Android

Briefcase Android uses Chaquopy to ship Python + the framework
wheel + every transitive dep.  Chaquopy maintains a curated index
of Android-built wheels for packages with native code; pure-Python
wheels just install via pip.  Packages that have neither hit the
build with a wheel-not-found error.

Our `[project] dependencies` is the source of truth for what
ships.  As of 1.5.0 the Android-relevant cuts are:

- **Pure-Python deps**: fastapi, anyio, sniffio, segno, pyyaml,
  ruamel.yaml, jinja2, aiofiles, python-dotenv, mcp, html2text,
  ddgs, model2vec, kohakuvault, openai, anthropic, httpx,
  starlette, prompt_toolkit, rich, textual → all should install
  cleanly via pip.
- **Has Android wheel in Chaquopy's index**: Pillow, numpy
  (transitive via model2vec), bcrypt, pydantic + pydantic-core,
  uvicorn (without `[standard]` extras), websockets, libcst
  (Chaquopy added Rust-backed wheel support in late 2024).
- **Desktop-only — moved to `[project.optional-dependencies].desktop`**:
  pywebview.  Desktop briefcase builds opt in via
  ``KohakuTerrarium[desktop]`` in their per-platform `requires`;
  the Android bundle doesn't pull it.
- **Heavy / uncertain**: pymupdf (PDF reading — only used by the
  `read_pdf` tool which is lazy-imported), gitpython (effectively
  unused by source today — `kt install` from git URLs is
  documented as deferred to 1.5.1).  If the Android build fails
  on either, drop them to optional and lazy-import remaining
  callers.

If the first real `briefcase build android` surfaces a missing
wheel, the fix path is:

1. Check whether the package is genuinely Android-incompatible
   (native deps that don't cross-compile) or whether Chaquopy
   simply lacks a wheel for that version.
2. For incompatible packages: move to an extras group, lazy-import
   the consumers, document the feature gap on mobile.
3. For "wheel just missing": pin to a version Chaquopy has, or
   contribute a wheel upstream to Chaquopy.

We can't preempt every wheel-resolution issue without running the
build; this README documents the strategy for when the first
build runs.

## Why a bundled sandbox?

Android has no `/bin/sh`.  Out of the box:

- `bash` tool → broken
- MCP stdio servers → broken
- `kt install` from git → broken
- `grep` / `glob` tools → fall back to slow pure-Python implementations

Shipping a single ~2MB busybox per ABI brings ~80% of the tool
catalog to working order on the phone — busybox provides `sh`,
`grep`, `find`, `sed`, `awk`, `curl`, and ~290 other commands as a
single multicall binary.  The framework's standalone `grep` /
`glob` tools are pure-Python already, so we don't need separate
ripgrep / fd.  `kt install` works via pip without git for PyPI +
tarball URLs; git-URL installs are deferred to 1.5.1 (likely via
pygit2 instead of a bundled git binary).

See `plans/1.5.0-roadmap/04-android-app/design.md` §4 for the full
tool-catalog matrix.
