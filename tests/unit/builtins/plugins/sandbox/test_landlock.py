"""Landlock write grants and the launcher that applies them (Linux)."""

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kohakuterrarium.builtins.plugins.sandbox import confine
from kohakuterrarium.builtins.plugins.sandbox import landlock
from kohakuterrarium.builtins.plugins.sandbox import landlock_exec
from kohakuterrarium.builtins.plugins.sandbox import plugin as plugin_mod
from kohakuterrarium.builtins.plugins.sandbox import roots
from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.modules.plugin.base import PluginBlockError
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


def _is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def _grants_file(data: bytes) -> int:
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    return read_fd


# Linux caps one argv element at MAX_ARG_STRLEN (32 pages, 128 KiB).
MAX_ARG_STRLEN = 128 * 1024


@pytest.fixture
def crowded(tmp_path, monkeypatch):
    """A workspace whose denied entry sits beside 5000 long-named siblings."""
    monkeypatch.setattr(roots, "temp_roots", lambda: [])
    monkeypatch.setattr(roots, "CACHE_ROOTS", ())
    workspace = Path(os.path.realpath(tmp_path)) / "workspace"
    workspace.mkdir()
    for i in range(5000):
        (workspace / f"sibling_with_a_long_name_{i:05d}").touch()
    denied = workspace / "zz" / "secret"
    denied.mkdir(parents=True)
    return workspace, denied


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
        argv = landlock.confine_argv(["ls", "-l"], 7)
        assert argv == [
            sys.executable,
            "-I",
            "-S",
            str(landlock.LAUNCHER),
            "--grants-fd",
            "7",
            "--",
            "ls",
            "-l",
        ]


@posix_paths
class TestGrantsHandOff:
    def test_grants_round_trip_through_the_launcher_reader(self):
        fd = landlock.grants_fd(["/w", "/dev"])
        assert landlock_exec.read_grants(fd) == ["/w", "/dev"]
        assert not _is_open(fd)

    def test_the_grants_fd_is_not_inherited_by_default(self):
        fd = landlock.grants_fd(["/w"])
        try:
            assert not os.get_inheritable(fd)
        finally:
            os.close(fd)

    def test_thousands_of_siblings_keep_argv_small(self, crowded):
        workspace, denied = crowded
        profile = _profile(fs_deny=(str(denied),))
        expected = landlock.write_grants(profile, workspace)
        assert len(json.dumps(expected)) > MAX_ARG_STRLEN
        command = confine.confine_command(
            ["/bin/true"], confine.LANDLOCK, profile, workspace
        )
        try:
            assert max(len(arg) for arg in command.argv) < 4096
            assert sum(len(arg) + 1 for arg in command.argv) < 8192
            assert command.grant_count == len(expected) > 5000
            (fd,) = command.pass_fds
            assert command.argv[4:7] == ["--grants-fd", str(fd), "--"]
            assert landlock_exec.read_grants(os.dup(fd)) == expected
        finally:
            command.close()
        assert not _is_open(fd)


class TestLauncher:
    def test_handled_rights_follow_the_kernel_abi(self):
        abi1 = landlock_exec.handled_writes(1)
        assert not abi1 & landlock_exec.REFER
        assert landlock_exec.handled_writes(2) == abi1 | landlock_exec.REFER
        assert landlock_exec.handled_writes(3) & landlock_exec.TRUNCATE

    def test_usage_error_without_separator(self, capsys):
        assert landlock_exec.main(["--grants-fd", "3", "true"]) == 2

    def test_usage_error_for_grants_on_the_command_line(self, capsys):
        assert landlock_exec.main(["[]", "--", "true"]) == 2
        assert "--grants-fd" in capsys.readouterr().err

    def test_fails_closed_when_the_policy_cannot_be_applied(self, monkeypatch, capsys):
        execs: list[list[str]] = []
        monkeypatch.setattr(landlock_exec, "landlock_abi", lambda: 0)
        monkeypatch.setattr(landlock_exec.os, "execvp", lambda f, a: execs.append(a))
        fd = _grants_file(b"[]")
        assert landlock_exec.main(["--grants-fd", str(fd), "--", "true"]) == 126
        assert execs == []
        assert "cannot apply Landlock policy" in capsys.readouterr().err

    @pytest.mark.parametrize("data", [b"not json", b'{"/w": 1}', b'["/w", 3]'])
    def test_rejects_a_malformed_grant_list(self, monkeypatch, data):
        applied: list[list[str]] = []
        monkeypatch.setattr(landlock_exec, "restrict_writes", applied.append)
        fd = _grants_file(data)
        assert landlock_exec.main(["--grants-fd", str(fd), "--", "true"]) == 126
        assert applied == []

    @pytest.mark.parametrize("fd", ["x", "-1", "999999"])
    def test_rejects_an_unusable_grants_fd(self, monkeypatch, fd):
        monkeypatch.setattr(landlock_exec, "restrict_writes", lambda paths: None)
        assert landlock_exec.main(["--grants-fd", fd, "--", "true"]) == 126

    def test_closes_the_grants_fd_before_exec(self, monkeypatch):
        applied: list[list[str]] = []
        execs: list[tuple[list[str], bool]] = []
        fd = _grants_file(json.dumps(["/w"]).encode())
        monkeypatch.setattr(landlock_exec, "restrict_writes", applied.append)
        monkeypatch.setattr(
            landlock_exec.os, "execvp", lambda f, a: execs.append((a, _is_open(fd)))
        )
        landlock_exec.main(["--grants-fd", str(fd), "--", "ls", "-l"])
        assert applied == [["/w"]]
        assert execs == [(["ls", "-l"], False)]

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
        return self._spawn(grants, ["/bin/sh", "-c", script])

    @staticmethod
    def _spawn(grants: list[str], command: list[str]) -> subprocess.CompletedProcess:
        fd = landlock.grants_fd(grants)
        try:
            argv = landlock.confine_argv(command, fd)
            return subprocess.run(argv, capture_output=True, timeout=30, pass_fds=(fd,))
        finally:
            os.close(fd)

    def test_the_command_does_not_inherit_the_grants_fd(self, tree):
        workspace, _ = tree
        result = self._run([str(workspace)], "ls -l /proc/self/fd/")
        assert result.returncode == 0, result.stderr
        assert b"kt-landlock-grants" not in result.stdout

    def test_thousands_of_siblings_around_a_denied_path(self, crowded):
        workspace, denied = crowded
        profile = _profile(fs_deny=(str(denied),))
        grants = landlock.write_grants(profile, workspace)
        result = self._run(
            grants,
            f"echo a > '{workspace}/sibling_with_a_long_name_00042' "
            f"&& echo b > '{denied}/b'",
        )
        assert result.returncode != 0
        assert b"Permission denied" in result.stderr
        assert (workspace / "sibling_with_a_long_name_00042").read_text() == "a\n"
        assert not (denied / "b").exists()

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
        result = self._spawn([str(workspace)], ["/nonexistent/kt-cmd"])
        assert result.returncode == 127


@posix_paths
class TestSpawnSite:
    """The sandbox plugin spawning a Landlock-confined command."""

    async def test_a_crowded_deny_path_still_spawns(self, crowded, monkeypatch):
        workspace, denied = crowded
        monkeypatch.setattr(plugin_mod, "confinement_backend", lambda: "landlock")
        result = await SandboxPlugin(fs_deny=[str(denied)]).run_subprocess_exec(
            ["/bin/true"], cwd=workspace, timeout=60
        )
        if landlock.landlock_available():
            assert result["returncode"] == 0, result["stderr"]
        else:
            # The launcher read every grant, then found no Landlock to apply.
            assert result["returncode"] == 126
            assert b"Landlock is not available" in result["stderr"]

    async def test_a_spawn_oserror_is_a_sandbox_error(self, crowded, monkeypatch):
        workspace, denied = crowded
        handed: list[tuple[int, ...]] = []

        async def refuse(argv, **kwargs):
            handed.append(kwargs["pass_fds"])
            raise OSError(errno.E2BIG, "Argument list too long", argv[0])

        monkeypatch.setattr(plugin_mod, "confinement_backend", lambda: "landlock")
        monkeypatch.setattr(plugin_mod, "_run_plain_subprocess", refuse)
        sandbox = SandboxPlugin(fs_deny=[str(denied)])
        expected = landlock.write_grants(sandbox._profile, workspace)
        with pytest.raises(PluginBlockError) as caught:
            await sandbox.run_subprocess_exec(["/bin/true"], cwd=workspace)
        message = str(caught.value)
        assert message.startswith("SandboxError[spawn]: ")
        assert "landlock" in message
        assert f"{len(expected)} write grants" in message
        assert "Argument list too long" in message
        assert isinstance(caught.value.__cause__, OSError)
        (fds,) = handed
        assert fds and not any(_is_open(fd) for fd in fds)

    async def test_the_grants_fd_is_closed_after_the_command_runs(
        self, crowded, monkeypatch
    ):
        workspace, denied = crowded
        handed: list[tuple[int, ...]] = []

        async def run(argv, **kwargs):
            handed.append(kwargs["pass_fds"])
            assert all(_is_open(fd) for fd in kwargs["pass_fds"])
            return {"returncode": 0, "stdout": b"", "stderr": b"", "timed_out": False}

        monkeypatch.setattr(plugin_mod, "confinement_backend", lambda: "landlock")
        monkeypatch.setattr(plugin_mod, "_run_plain_subprocess", run)
        await SandboxPlugin(fs_deny=[str(denied)]).run_subprocess_exec(
            ["/bin/true"], cwd=workspace
        )
        (fds,) = handed
        assert len(fds) == 1 and not _is_open(fds[0])

    async def test_an_unconfined_spawn_error_is_left_alone(self, tmp_path, monkeypatch):
        monkeypatch.setattr(plugin_mod, "confinement_backend", lambda: "landlock")
        with pytest.raises(FileNotFoundError):
            await SandboxPlugin(os_enforce="off").run_subprocess_exec(
                ["/nonexistent/kt-cmd"], cwd=tmp_path
            )
