"""Unit tests for ``utils.mobile_sandbox``.

These tests pin the resolution contract — given an environment +
filesystem shape, what does each helper return?  The runtime
extraction path is exercised under a fake assets dir so we don't
need a real APK / Android device.
"""

import json
import stat
import sys
from pathlib import Path

import pytest

from kohakuterrarium.utils import mobile_sandbox

# Windows ignores the POSIX exec bits — ``Path.chmod`` is effectively
# a no-op for ``S_IXUSR`` etc. on win32.  Tests that assert the
# extracted binary is executable need to skip there; the runtime
# code under test still calls ``chmod`` (it's a one-line cost) so
# the Android invocation path is unchanged.
_CAN_OBSERVE_EXEC_BITS = sys.platform != "win32"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    # Strip any env vars from the test runner's shell that might
    # accidentally point this module at a real bin dir.
    for key in (
        "KT_PROFILE",
        "KT_SANDBOX_BIN_DIR",
        "KT_SANDBOX_ASSETS_DIR",
        "KT_CONFIG_DIR",
    ):
        monkeypatch.delenv(key, raising=False)


class TestIsMobileProfile:
    def test_default_off(self):
        assert mobile_sandbox.is_mobile_profile() is False

    def test_mobile_lowercase(self, monkeypatch):
        monkeypatch.setenv("KT_PROFILE", "mobile")
        assert mobile_sandbox.is_mobile_profile() is True

    def test_mobile_mixed_case(self, monkeypatch):
        monkeypatch.setenv("KT_PROFILE", " Mobile ")
        assert mobile_sandbox.is_mobile_profile() is True

    def test_other_profile_off(self, monkeypatch):
        monkeypatch.setenv("KT_PROFILE", "desktop")
        assert mobile_sandbox.is_mobile_profile() is False


class TestSandboxBinDir:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        bin_dir = tmp_path / "explicit"
        bin_dir.mkdir()
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(bin_dir))
        # KT_CONFIG_DIR also set — env override must beat it.
        other = tmp_path / "config"
        (other / "bin").mkdir(parents=True)
        monkeypatch.setenv("KT_CONFIG_DIR", str(other))
        assert mobile_sandbox.sandbox_bin_dir() == bin_dir

    def test_env_override_missing_dir_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path / "does-not-exist"))
        assert mobile_sandbox.sandbox_bin_dir() is None

    def test_config_dir_bin_subdir(self, monkeypatch, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        assert mobile_sandbox.sandbox_bin_dir() == bin_dir

    def test_no_env_no_dir_returns_none(self, monkeypatch, tmp_path):
        # ``~/.kohakuterrarium/bin`` is the last-resort fallback; we
        # can't easily mock ``Path.home()`` portably, so the test
        # only asserts the negative — when neither env var is set
        # and we can't read the user's home (or it doesn't have
        # the bin dir), the helper returns None.
        # Mock home to a guaranteed-empty tmpdir.
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "noexist")
        assert mobile_sandbox.sandbox_bin_dir() is None


class TestSandboxBinary:
    def test_resolves_canonical_short_name(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        (tmp_path / "busybox").write_bytes(b"#!fake\n")
        # ``sh`` and ``busybox`` both map to busybox.
        assert mobile_sandbox.sandbox_binary("sh") == tmp_path / "busybox"
        assert mobile_sandbox.sandbox_binary("busybox") == tmp_path / "busybox"

    def test_busybox_applet_aliases(self, monkeypatch, tmp_path):
        # Every shell-applet short name resolves to the single
        # busybox multicall binary — busybox dispatches via argv[0]
        # at runtime.  Pin so a contributor doesn't accidentally add
        # a separate binary for one of these without bundling it.
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        (tmp_path / "busybox").write_bytes(b"#!fake\n")
        for name in ("sh", "bash", "grep", "find", "sed", "awk", "curl"):
            resolved = mobile_sandbox.sandbox_binary(name)
            assert (
                resolved == tmp_path / "busybox"
            ), f"{name!r} should resolve to bundled busybox; got {resolved}"

    def test_unknown_name_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        (tmp_path / "busybox").write_bytes(b"#!fake\n")
        assert mobile_sandbox.sandbox_binary("notarealtool") is None

    def test_missing_binary_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        # Bin dir exists but busybox isn't extracted yet.
        assert mobile_sandbox.sandbox_binary("sh") is None


class TestDefaultWorkdir:
    def test_off_mobile_returns_cwd(self, monkeypatch, tmp_path):
        # Without ``KT_PROFILE=mobile`` we keep the historical
        # ``Path.cwd()`` behaviour — desktop operators launched
        # ``kt run`` from a directory and expect that directory to
        # be the agent's workspace.
        monkeypatch.chdir(tmp_path)
        assert mobile_sandbox.default_workdir() == Path(tmp_path).resolve()

    def test_mobile_returns_config_work_subdir(self, monkeypatch, tmp_path):
        # On mobile, ``cwd`` is ``/`` (Briefcase boots Python there)
        # and unwritable.  The helper redirects to
        # ``<KT_CONFIG_DIR>/work/`` which Java guarantees is writable.
        monkeypatch.setenv("KT_PROFILE", "mobile")
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        out = mobile_sandbox.default_workdir()
        assert out == tmp_path / "work"
        # And the helper creates it so callers can write immediately.
        assert out.is_dir()

    def test_mobile_creates_workdir_lazily(self, monkeypatch, tmp_path):
        # The dir doesn't exist before the first call.
        monkeypatch.setenv("KT_PROFILE", "mobile")
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        target = tmp_path / "work"
        assert not target.exists()
        mobile_sandbox.default_workdir()
        assert target.is_dir()

    def test_mobile_without_config_dir_falls_back(self, monkeypatch, tmp_path):
        # Defensive: mobile profile is set but Java forgot to populate
        # ``KT_CONFIG_DIR``.  Falls back to ``Path.cwd()`` rather than
        # raising — the executor's downstream consumer at least sees
        # a Path object.
        monkeypatch.setenv("KT_PROFILE", "mobile")
        monkeypatch.chdir(tmp_path)
        assert mobile_sandbox.default_workdir() == Path(tmp_path).resolve()

    def test_mobile_mkdir_failure_falls_back(self, monkeypatch, tmp_path):
        # If ``<config>/work`` can't be created (e.g. permission
        # flap), the helper logs + falls back to cwd so the caller
        # never gets a path it can't use.
        monkeypatch.setenv("KT_PROFILE", "mobile")
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))

        original_mkdir = Path.mkdir

        def _boom(self, *args, **kwargs):
            if self == tmp_path / "work":
                raise OSError("mkdir refused")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", _boom)
        monkeypatch.chdir(tmp_path)
        out = mobile_sandbox.default_workdir()
        # Fell back to cwd.
        assert out == Path(tmp_path).resolve()


class TestBundledShCommand:
    def test_returns_argv_when_busybox_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        (tmp_path / "busybox").write_bytes(b"#!fake\n")
        argv = mobile_sandbox.bundled_sh_command("echo hi")
        assert argv is not None
        # New shape: argv[0] is the LITERAL string "busybox" — not a
        # path.  The caller passes the actual executable path via
        # ``subprocess.Popen(executable=…)`` from
        # :func:`bundled_sh_exe`.  This split is required on Android
        # where the on-disk file is ``libbusybox.so`` but busybox's
        # multicall dispatcher needs ``argv[0]="busybox"`` to find
        # its own applet table.
        assert argv == ["busybox", "sh", "-c", "echo hi"]

    def test_returns_none_without_busybox(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        assert mobile_sandbox.bundled_sh_command("echo hi") is None


class TestBundledShExe:
    def test_returns_path_when_busybox_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        (tmp_path / "busybox").write_bytes(b"#!fake\n")
        exe = mobile_sandbox.bundled_sh_exe()
        assert exe == tmp_path / "busybox"

    def test_prefers_libbusybox_so_when_both_present(self, monkeypatch, tmp_path):
        # Native-library layout (Android) wins over the legacy
        # ``busybox`` name when both are populated — the
        # ``libbusybox.so`` form is the only one that survives
        # Android's W^X policy because PackageManager extracts it
        # into the execute-allowed nativeLibraryDir.
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        (tmp_path / "libbusybox.so").write_bytes(b"#!fake-native-lib\n")
        (tmp_path / "busybox").write_bytes(b"#!fake-legacy\n")
        assert mobile_sandbox.bundled_sh_exe() == tmp_path / "libbusybox.so"

    def test_falls_back_to_legacy_busybox(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        (tmp_path / "busybox").write_bytes(b"#!fake\n")
        assert mobile_sandbox.bundled_sh_exe() == tmp_path / "busybox"

    def test_returns_none_when_neither_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        assert mobile_sandbox.bundled_sh_exe() is None


class TestSandboxBinaryNativeLibLayout:
    def test_resolves_libbusybox_so_for_every_applet(self, monkeypatch, tmp_path):
        # Pin that every shell-applet short name finds the bundled
        # ``libbusybox.so`` — busybox dispatches via argv[0] at
        # runtime so the same multicall file backs every name.
        monkeypatch.setenv("KT_SANDBOX_BIN_DIR", str(tmp_path))
        (tmp_path / "libbusybox.so").write_bytes(b"#!fake-native-lib\n")
        for name in ("sh", "bash", "grep", "find", "sed", "awk", "curl"):
            resolved = mobile_sandbox.sandbox_binary(name)
            assert resolved == tmp_path / "libbusybox.so", (
                f"{name!r} should resolve to bundled libbusybox.so; " f"got {resolved}"
            )


class TestEnsureExtracted:
    def _make_assets(self, root: Path, binaries: list[str]) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(
            json.dumps({"binaries": binaries}), encoding="utf-8"
        )
        for name in binaries:
            (root / name).write_bytes(b"#!fake-static-binary\n")

    def test_no_assets_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        # No ``KT_SANDBOX_ASSETS_DIR`` set → returns None silently.
        assert mobile_sandbox.ensure_extracted() is None

    def test_first_launch_extracts_and_chmod(self, monkeypatch, tmp_path):
        assets = tmp_path / "assets"
        self._make_assets(assets, ["busybox", "rg"])
        dest = tmp_path / "bin"
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("KT_SANDBOX_ASSETS_DIR", str(assets))

        out = mobile_sandbox.ensure_extracted()
        assert out == dest
        # Both binaries present.  Exec-bit check skipped on Windows
        # (POSIX bits aren't honoured there); the runtime still calls
        # ``chmod`` which is a no-op on win32 and a real grant on
        # Android, where this matters.
        for name in ("busybox", "rg"):
            target = dest / name
            assert target.is_file()
            if _CAN_OBSERVE_EXEC_BITS:
                assert target.stat().st_mode & stat.S_IXUSR

    @pytest.mark.skipif(
        not _CAN_OBSERVE_EXEC_BITS,
        reason="idempotency check probes the executable bit which "
        "Windows does not honour; the runtime path under test still "
        "works on Android",
    )
    def test_second_launch_is_idempotent(self, monkeypatch, tmp_path):
        assets = tmp_path / "assets"
        self._make_assets(assets, ["busybox"])
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("KT_SANDBOX_ASSETS_DIR", str(assets))

        first = mobile_sandbox.ensure_extracted()
        assert first is not None
        # Tamper with the asset to confirm we DON'T re-extract.
        (first / "busybox").write_bytes(b"#!modified-do-not-overwrite\n")
        (first / "busybox").chmod(
            (first / "busybox").stat().st_mode
            | stat.S_IXUSR
            | stat.S_IXGRP
            | stat.S_IXOTH
        )

        out = mobile_sandbox.ensure_extracted()
        assert out == first
        # The "modified" content survived → extraction was skipped.
        assert (first / "busybox").read_bytes().startswith(b"#!modified")

    def test_partial_assets_returns_none(self, monkeypatch, tmp_path):
        # Tightened contract (audit fix): if no single layout
        # contains EVERY declared binary, refuse — partial
        # extraction is a foot-gun.  An operator gets a clean
        # "nothing extracted" log instead of a half-bin dir that
        # the bash tool would later trip over.
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "manifest.json").write_text(
            json.dumps({"binaries": ["busybox", "extra-tool"]}),
            encoding="utf-8",
        )
        (assets / "busybox").write_bytes(b"#!fake\n")
        # extra-tool missing — no flat layout is complete, no per-
        # ABI subdir exists either.
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("KT_SANDBOX_ASSETS_DIR", str(assets))

        dest = mobile_sandbox.ensure_extracted()
        assert dest is None

    def test_unreadable_manifest_returns_none(self, monkeypatch, tmp_path):
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "manifest.json").write_text("not { valid json", encoding="utf-8")
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("KT_SANDBOX_ASSETS_DIR", str(assets))
        assert mobile_sandbox.ensure_extracted() is None

    def test_per_abi_layout_with_env_abi(self, monkeypatch, tmp_path):
        # Real APK asset shape: manifest.json at root + binaries
        # under <abi>/.  Verifies the layout-probing branch finds
        # the right ABI subdir.
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "manifest.json").write_text(
            json.dumps(
                {
                    "binaries": ["busybox"],
                    "abis": ["arm64-v8a", "armeabi-v7a", "x86_64"],
                }
            ),
            encoding="utf-8",
        )
        (assets / "arm64-v8a").mkdir()
        (assets / "arm64-v8a" / "busybox").write_bytes(b"#!arm64\n")
        (assets / "x86_64").mkdir()
        (assets / "x86_64" / "busybox").write_bytes(b"#!x86_64\n")
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("KT_SANDBOX_ASSETS_DIR", str(assets))
        monkeypatch.setenv("KT_SANDBOX_ABI", "arm64-v8a")

        dest = mobile_sandbox.ensure_extracted()
        assert dest is not None
        # Must have picked the arm64 binary, not the x86_64 one.
        assert (dest / "busybox").read_bytes() == b"#!arm64\n"

    def test_per_abi_layout_falls_back_to_manifest_abis(self, monkeypatch, tmp_path):
        # No KT_SANDBOX_ABI set; the helper walks the manifest's
        # ``abis`` list in order and picks the first match present.
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "manifest.json").write_text(
            json.dumps(
                {
                    "binaries": ["busybox"],
                    "abis": ["arm64-v8a", "armeabi-v7a", "x86_64"],
                }
            ),
            encoding="utf-8",
        )
        # Only ship armeabi-v7a (second in list) — first wins it.
        (assets / "armeabi-v7a").mkdir()
        (assets / "armeabi-v7a" / "busybox").write_bytes(b"#!armv7\n")
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("KT_SANDBOX_ASSETS_DIR", str(assets))

        dest = mobile_sandbox.ensure_extracted()
        assert dest is not None
        assert (dest / "busybox").read_bytes() == b"#!armv7\n"

    def test_explicit_abi_arg_beats_env(self, monkeypatch, tmp_path):
        # The function signature accepts ``abi=`` so callers can
        # override the env when they know better.  Pin precedence.
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "manifest.json").write_text(
            json.dumps({"binaries": ["busybox"], "abis": ["arm64-v8a"]}),
            encoding="utf-8",
        )
        (assets / "x86_64").mkdir()
        (assets / "x86_64" / "busybox").write_bytes(b"#!x86\n")
        (assets / "arm64-v8a").mkdir()
        (assets / "arm64-v8a" / "busybox").write_bytes(b"#!arm\n")
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("KT_SANDBOX_ASSETS_DIR", str(assets))
        monkeypatch.setenv("KT_SANDBOX_ABI", "x86_64")

        dest = mobile_sandbox.ensure_extracted(abi="arm64-v8a")
        assert dest is not None
        # Explicit arg wins over env — arm binary extracted.
        assert (dest / "busybox").read_bytes() == b"#!arm\n"
