"""Landlock write grants and the launcher that applies them (Linux)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kohakuterrarium.builtins.plugins.sandbox import landlock
from kohakuterrarium.builtins.plugins.sandbox import landlock_exec
from kohakuterrarium.builtins.plugins.sandbox import roots
from kohakuterrarium.modules.sandbox.presets import WORKSPACE

posix_paths = pytest.mark.skipif(
    sys.platform == "win32", reason="Landlock grants are POSIX paths"
)
needs_landlock = pytest.mark.skipif(
    not landlock.landlock_available(), reason="Landlock is unavailable on this host"
)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A workspace, plus a home with a secret beside ordinary entries."""
    monkeypatch.setattr(roots, "temp_roots", lambda: [])
    monkeypatch.setattr(roots, "CACHE_ROOTS", ())
    base = Path(os.path.realpath(tmp_path))
    workspace = base / "workspace"
    home = base / "home"
    (home / "config" / "git").mkdir(parents=True)
    (home / "config" / "other").mkdir()
    (home / "notes.txt").write_text("keep\n", encoding="utf-8")
    workspace.mkdir()
    return workspace, home


def _profile(**overrides):
    return WORKSPACE.with_overrides(**{"fs_deny": (), **overrides})


@posix_paths
class TestWriteGrants:
    def test_workspace_is_granted_whole(self, tree):
        workspace, _ = tree
        grants = landlock.write_grants(_profile(), workspace)
        assert str(workspace) in grants
        assert "/dev" in grants

    def test_a_denied_path_inside_a_root_is_carved_out(self, tree):
        workspace, home = tree
        secret = home / "config" / "git"
        profile = _profile(fs_write="broad", fs_deny=(str(secret),))
        grants = landlock.write_grants(profile, workspace)
        assert str(secret) not in grants
        # Its ancestors are not granted, so it cannot be renamed away...
        for ancestor in [home / "config", home, *home.parents]:
            assert str(ancestor) not in grants
        # ...while every entry beside it on the way down stays writable.
        assert str(home / "config" / "other") in grants
        assert str(home / "notes.txt") in grants
        assert str(workspace) in grants

    def test_symlinked_entries_are_not_followed(self, tree, tmp_path):
        workspace, home = tree
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (home / "link").symlink_to(elsewhere)
        profile = _profile(fs_write="broad", fs_deny=(str(home / "config"),))
        grants = landlock.write_grants(profile, workspace)
        assert str(home / "link") not in grants

    def test_write_allow_is_granted_inside_the_deny_list(self, tree):
        workspace, home = tree
        sockets = home / "config" / "git"
        profile = _profile(fs_deny=(str(home),))
        grants = landlock.write_grants(profile, workspace, write_allow=[str(sockets)])
        assert str(sockets) in grants
        assert str(home) not in grants

    def test_deny_level_grants_devices_only(self, tree):
        workspace, home = tree
        profile = _profile(fs_write="deny")
        grants = landlock.write_grants(profile, workspace, write_allow=[str(home)])
        assert grants == ["/dev"]

    def test_a_workspace_inside_the_deny_list_gets_nothing(self, tree):
        _, home = tree
        profile = _profile(fs_deny=(str(home),))
        grants = landlock.write_grants(profile, home / "config")
        assert grants == ["/dev"]

    def test_tags_in_the_deny_list_are_ignored(self, tree):
        workspace, _ = tree
        profile = _profile(fs_deny=("%secrets",))
        assert str(workspace) in landlock.write_grants(profile, workspace)

    def test_confine_argv_runs_the_launcher_by_path(self):
        argv = landlock.confine_argv(["ls", "-l"], ["/w"])
        assert argv == [
            sys.executable,
            "-I",
            "-S",
            str(landlock.LAUNCHER),
            json.dumps(["/w"]),
            "--",
            "ls",
            "-l",
        ]


class TestLauncher:
    def test_handled_rights_follow_the_kernel_abi(self):
        abi1 = landlock_exec.handled_writes(1)
        assert not abi1 & landlock_exec.REFER
        assert landlock_exec.handled_writes(2) == abi1 | landlock_exec.REFER
        assert landlock_exec.handled_writes(3) & landlock_exec.TRUNCATE

    def test_usage_error_without_separator(self, capsys):
        assert landlock_exec.main(["[]", "true"]) == 2

    def test_fails_closed_when_the_policy_cannot_be_applied(self, monkeypatch, capsys):
        execs: list[list[str]] = []
        monkeypatch.setattr(landlock_exec, "landlock_abi", lambda: 0)
        monkeypatch.setattr(landlock_exec.os, "execvp", lambda f, a: execs.append(a))
        assert landlock_exec.main(["[]", "--", "true"]) == 126
        assert execs == []
        assert "cannot apply Landlock policy" in capsys.readouterr().err

    def test_rejects_a_malformed_grant_list(self, monkeypatch):
        monkeypatch.setattr(landlock_exec, "landlock_abi", lambda: 1)
        assert landlock_exec.main(["not json", "--", "true"]) == 126

    @pytest.mark.skipif(
        sys.platform.startswith("linux"), reason="non-Linux hosts report no ABI"
    )
    def test_no_abi_off_linux(self):
        assert landlock_exec.landlock_abi() == 0


@pytest.mark.skipif(
    not (sys.platform.startswith("linux") and os.environ.get("GITHUB_ACTIONS")),
    reason="only CI Linux runners must have Landlock",
)
def test_ci_linux_runners_exercise_landlock():
    assert landlock.landlock_available(), "kernel tests would silently skip"


@needs_landlock
class TestKernel:
    def _run(self, grants: list[str], script: str) -> subprocess.CompletedProcess:
        argv = landlock.confine_argv(["/bin/sh", "-c", script], grants)
        return subprocess.run(argv, capture_output=True, timeout=30)

    def test_writes_land_only_in_granted_directories(self, tree):
        workspace, home = tree
        result = self._run(
            [str(workspace)],
            f"echo a > '{workspace}/a' && echo b > '{home}/b'",
        )
        assert result.returncode != 0
        assert b"Permission denied" in result.stderr
        assert (workspace / "a").read_text(encoding="utf-8") == "a\n"
        assert not (home / "b").exists()

    def test_a_granted_file_is_writable_but_not_removable(self, tree):
        workspace, home = tree
        notes = home / "notes.txt"
        result = self._run([str(notes)], f"echo more >> '{notes}' && rm '{notes}'")
        assert result.returncode != 0
        assert notes.read_text(encoding="utf-8") == "keep\nmore\n"

    def test_truncating_outside_the_grants_is_refused(self, tree):
        if landlock_exec.landlock_abi() < 3:
            pytest.skip("truncate(2) is only restricted from Landlock ABI 3")
        workspace, home = tree
        notes = home / "notes.txt"
        result = self._run(
            [str(workspace)],
            f"{sys.executable} -c \"import os; os.truncate('{notes}', 0)\"",
        )
        assert result.returncode != 0
        assert notes.read_text(encoding="utf-8") == "keep\n"

    def test_a_symlink_inside_a_grant_does_not_reach_outside(self, tree):
        workspace, home = tree
        (workspace / "out").symlink_to(home)
        result = self._run([str(workspace)], f"echo x > '{workspace}/out/new'")
        assert result.returncode != 0
        assert not (home / "new").exists()

    def test_renames_inside_the_grants_work(self, tree):
        if landlock_exec.landlock_abi() < 2:
            pytest.skip("ABI 1 refuses every cross-directory rename")
        workspace, _ = tree
        (workspace / "d").mkdir()
        result = self._run(
            [str(workspace)],
            f"echo x > '{workspace}/d/f' && mv '{workspace}/d/f' '{workspace}/g'",
        )
        assert result.returncode == 0, result.stderr
        assert (workspace / "g").exists()

    def test_missing_command_reports_127(self, tree):
        workspace, _ = tree
        argv = landlock.confine_argv(["/nonexistent/kt-cmd"], [str(workspace)])
        result = subprocess.run(argv, capture_output=True, timeout=30)
        assert result.returncode == 127
