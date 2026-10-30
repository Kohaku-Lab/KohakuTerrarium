"""Unit tests for the launcher bootloader's ``prepare`` orchestration.

The GUI backends are blocked so the first-launch splash runs headless;
everything else (settings, update lock, bundled-release extract, smoke,
pointer swap) is the real launcher code against a temp config dir.
"""

import io
import json
import sys
import tarfile
import types

import pytest

from kohakuterrarium.launcher import bootloader
from kohakuterrarium.launcher import paths as _paths
from kohakuterrarium.launcher import settings as _settings
from kohakuterrarium.launcher import tree_ops as _tree
from kohakuterrarium.launcher import update_runner as _runner
from kohakuterrarium.launcher.feeds import FeedError


def _make_release_tarball(path, *, version: str) -> None:
    members = {
        "manifest.json": json.dumps({"version": version, "build_id": "tb"}).encode(),
        "site-packages/kohakuterrarium/__init__.py": (
            f'__version__ = "{version}"\n'.encode()
        ),
    }
    with tarfile.open(str(path), mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


@pytest.fixture
def headless(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setitem(sys.modules, "tkinter", None)
    # The terminal-frame linger is UX only; keep the suite fast.
    monkeypatch.setattr(bootloader, "time", types.SimpleNamespace(sleep=lambda s: None))
    return tmp_path


class TestPrepare:
    def test_first_launch_installs_bundled_release_then_reuses_it(
        self, monkeypatch, headless
    ):
        bundled = headless / "bundled-release"
        bundled.mkdir()
        _make_release_tarball(
            bundled / "kohakuterrarium-9.9.9-win-x64-py3.13.tar.gz", version="9.9.9"
        )
        monkeypatch.setattr(
            _paths, "_candidate_bundled_release_dirs", lambda: [bundled]
        )

        result = bootloader.prepare([])

        assert result.done is False
        assert result.exit_code == 0
        assert result.error is None
        assert result.version == "9.9.9"
        expected_site = _paths.site_packages_dir(_paths.version_dir("9.9.9"))
        assert result.site_packages == expected_site
        assert (expected_site / "kohakuterrarium" / "__init__.py").is_file()
        assert _tree.read_active_pointer().version == "9.9.9"
        assert _settings.load().runtime.active_version == "9.9.9"

        # Second launch: pointer present, notify-on-launch default → no
        # install work, same tree handed back.
        again = bootloader.prepare([])
        assert again.done is False
        assert again.site_packages == expected_site
        assert again.version == "9.9.9"

    def test_first_launch_failure_surfaces_exit_code_5(self, monkeypatch, headless):
        monkeypatch.setattr(_paths, "_candidate_bundled_release_dirs", lambda: [])

        def offline(cfg, **_):
            raise FeedError("manifest fetch network error: offline")

        monkeypatch.setattr(_runner, "resolve_feed", offline)

        result = bootloader.prepare([])

        assert result.done is False
        assert result.exit_code == 5
        assert result.site_packages is None
        assert "offline" in (result.error or "")
        assert _tree.read_active_pointer() is None
        assert "offline" in (_settings.load().runtime.last_check_error or "")

    def test_caller_supplied_progress_replaces_the_splash(self, monkeypatch, headless):
        bundled = headless / "bundled-release"
        bundled.mkdir()
        _make_release_tarball(
            bundled / "kohakuterrarium-9.9.9-win-x64-py3.13.tar.gz", version="9.9.9"
        )
        monkeypatch.setattr(
            _paths, "_candidate_bundled_release_dirs", lambda: [bundled]
        )
        monkeypatch.setattr(
            bootloader,
            "run_with_splash",
            lambda *_a, **_k: pytest.fail("splash used despite a progress callback"),
        )
        seen = []

        result = bootloader.prepare(
            [], progress=lambda phase, percent, message: seen.append((phase, percent))
        )

        assert result.version == "9.9.9"
        assert seen and seen[-1][1] >= 90

    def test_bundled_release_dir_can_be_named_by_the_environment(
        self, monkeypatch, tmp_path
    ):
        named = tmp_path / "named"
        named.mkdir()
        _make_release_tarball(
            named / "kohakuterrarium-1.2.3-win-x64-py3.13.tar.gz", version="1.2.3"
        )
        monkeypatch.setenv("KT_BUNDLED_RELEASE_DIR", str(named))

        assert _paths.bundled_release_dir() == named

    def test_windows_waits_on_the_cli_instead_of_exec(self, monkeypatch):
        monkeypatch.setattr(bootloader, "_IS_WINDOWS", True)
        monkeypatch.setattr(
            bootloader.os,
            "execv",
            lambda *_a: pytest.fail("execv returns control to the shell on Windows"),
        )
        ran = []
        monkeypatch.setattr(
            bootloader.subprocess, "call", lambda command: ran.append(command) or 7
        )

        assert bootloader._hand_off(["python", "-m", "kohakuterrarium.cli"]) == 7
        assert ran == [["python", "-m", "kohakuterrarium.cli"]]

    def test_posix_replaces_the_process(self, monkeypatch):
        monkeypatch.setattr(bootloader, "_IS_WINDOWS", False)
        replaced = []
        monkeypatch.setattr(
            bootloader.os, "execv", lambda path, argv: replaced.append((path, argv))
        )

        bootloader._hand_off(["python", "-m", "kohakuterrarium.cli", "--help"])

        assert replaced == [
            ("python", ["python", "-m", "kohakuterrarium.cli", "--help"])
        ]

    def test_splash_demo_is_a_one_shot_mode(self, headless):
        result = bootloader.prepare(["--splash-demo"])
        assert result.done is True
        assert result.exit_code == 0
        assert result.site_packages is None
