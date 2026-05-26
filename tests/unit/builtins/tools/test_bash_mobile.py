"""Bash-tool mobile-profile resolution tests.

Pins the shell-executable resolver's behaviour on Android:

- ``/system/bin/sh`` (stock mksh) wins when present
- Bundled ``libbusybox.so`` is the fallback when ``/system`` isn't
  populated (covers non-Android mobile-profile testing on desktop)
- Falls through to the host PATH lookup on non-mobile profiles
"""

from pathlib import Path

import pytest

from kohakuterrarium.builtins.tools import bash as bash_mod


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    # Strip mobile-profile / sandbox env vars between tests so one
    # test's setup doesn't leak into the next via the cached
    # ``_AVAILABLE_SHELLS`` or env-var reads.
    for key in (
        "KT_PROFILE",
        "KT_SANDBOX_BIN_DIR",
        "KT_SHELL_PATH",
        "KT_BASH_PATH",
        "KT_SH_PATH",
        "KT_ZSH_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    # Force a fresh ``_get_available_shells`` evaluation.
    monkeypatch.setattr(bash_mod, "_AVAILABLE_SHELLS", None)


def _patched_is_file(present_paths: set[str]):
    """Return a ``Path.is_file`` replacement that reports True for a
    fixed set of paths and delegates to the real check for everything
    else.

    The real-check delegate is critical: ``_resolve_shell_executable``
    also probes the sandbox binary dir (``Path / "libbusybox.so"``,
    etc.) via ``.is_file()`` — if we replaced is_file with a blanket
    False, the bundled-fallback test would never find the bin it
    just wrote to ``tmp_path``.
    """
    real_is_file = Path.is_file
    # Normalise both sides through ``Path(str).as_posix()`` so the
    # comparison works on Windows where the hardcoded "/system/bin/sh"
    # gets re-rendered with backslashes by WindowsPath.
    targets = {Path(p).as_posix() for p in present_paths}

    def _fake(self):
        if self.as_posix() in targets:
            return True
        return real_is_file(self)

    return _fake


class TestResolveShellExecutableMobile:
    def test_prefers_system_bin_sh_on_mobile(self, monkeypatch):
        monkeypatch.setenv("KT_PROFILE", "mobile")
        monkeypatch.setattr(Path, "is_file", _patched_is_file({"/system/bin/sh"}))

        for shell_type in ("bash", "sh", "zsh"):
            out = bash_mod._resolve_shell_executable(shell_type)
            assert out is not None
            # Normalise to posix so Windows (where ``str(Path)``
            # renders with backslashes) doesn't fail the assertion —
            # only the semantic path matters here, not the separator
            # the host happens to render.
            assert Path(out).as_posix() == "/system/bin/sh", (
                f"{shell_type} should resolve to /system/bin/sh on mobile; "
                f"got {out!r}"
            )

    def test_falls_back_to_bundled_when_no_system_sh(self, monkeypatch, tmp_path):
        # Non-Android mobile-profile (dev / emulator sideload): no
        # /system/bin/sh, but the sandbox dir holds libbusybox.so.
        monkeypatch.setenv("KT_PROFILE", "mobile")
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        (tmp_path / "libbusybox.so").write_bytes(b"#!fake\n")

        # /system/bin/sh deliberately NOT in the present set — real
        # filesystem check delegates for the sandbox bin itself.
        monkeypatch.setattr(Path, "is_file", _patched_is_file(set()))

        out = bash_mod._resolve_shell_executable("sh")
        assert out is not None
        assert Path(out).name == "libbusybox.so"

    def test_non_mobile_ignores_system_bin_sh(self, monkeypatch):
        # On a non-mobile profile, the resolver must NOT short-circuit
        # to /system/bin/sh — even if it happens to exist on this host
        # (e.g. you ran the test inside Termux).  The desktop path
        # uses operator-override env + shutil.which lookup.
        # KT_PROFILE deliberately not set.
        monkeypatch.setenv("KT_SHELL_PATH", "/usr/local/bin/myshell")
        monkeypatch.setattr(
            bash_mod.shutil, "which", lambda exe: "/usr/local/bin/myshell"
        )
        # Even if /system/bin/sh existed, we shouldn't pick it.
        monkeypatch.setattr(Path, "is_file", _patched_is_file({"/system/bin/sh"}))
        out = bash_mod._resolve_shell_executable("bash")
        assert out == "/usr/local/bin/myshell"
