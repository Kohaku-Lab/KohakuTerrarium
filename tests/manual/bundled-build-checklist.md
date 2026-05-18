# Manual smoke checklist — bundled-first install (topic 06 / 01)

Run this checklist before publishing a 1.5.x desktop release.  The
automated `scripts/verify_bundled_wheels.py` gate catches missing
wheels but cannot prove that an **offline first launch** actually
works end-to-end on a fresh machine.

## Per-platform: produce + smoke

For each of macOS, Windows, Linux (AppImage):

1. **Build cleanly**:
   ```
   python scripts/build_wrapper_wheels.py --from-source --out wheels-bundle
   briefcase create
   briefcase build
   briefcase package        # (--adhoc-sign on macOS for dev)
   python scripts/verify_bundled_wheels.py <built-artifact-or-dir>
   ```
   The verify step must exit 0.

2. **Install on a clean VM** with NO network:
   - macOS: drag `.app` into `/Applications` on a fresh macOS VM.
   - Windows: double-click the `.msi` on a fresh Windows VM.
   - Linux: download the `.AppImage`, `chmod +x`, run.

3. **First launch (offline)**:
   - Splash window appears.
   - Splash shows "Installing framework" stage briefly (bundled wheels
     install faster than PyPI).
   - Main app loads to the chat UI.
   - No "no internet" / "pip install failed" error anywhere.

4. **Verify install source**:
   - Open `Settings → Updates`.
   - Status line reads: `Installed: 1.5.x (from bundled offline copy)`.
   - The "Update" button shows `Update to 1.5.x from PyPI` IF the
     configured source is PyPI (default).  If `source.kind = "bundled"`
     was explicitly set, the button reads `Reinstall from bundled
     (same version)`.

5. **Reconnect network, click "Check now"**:
   - Status updates to show the latest PyPI version.
   - If 1.5.x+1 is out, the banner offers an update.

6. **Click "Update"**:
   - Progress modal opens.
   - Install completes.
   - Status updates: `Installed: 1.5.x+1 (from PyPI)`.

7. **Click "Rollback"**:
   - Wrapper swaps back to `venv.old/`.
   - Status updates: `Installed: 1.5.x (from bundled offline copy)`
     (the `install_source` field is restored from the previous venv's
     settings snapshot).

## Anti-checks (these MUST still happen)

- Dev install (`python -m kohakuterrarium.launcher` from a repo checkout
  WITHOUT a `wheels-bundle/` directory): falls through to PyPI per
  `cfg.source.kind = "pypi"`.  No "bundled wheels missing" hard error.
- User-installed wrapper that explicitly set `source.kind = "git"`
  in settings: first launch uses git, NOT bundled (`_is_default_source`
  returns False).

## Sign-off

Record platform / build SHA / pass-fail in the release issue.
Failures here block the publish.
