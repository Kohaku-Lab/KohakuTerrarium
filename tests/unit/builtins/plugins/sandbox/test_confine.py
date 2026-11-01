"""Kernel write confinement of sandboxed subprocesses, whichever backend runs."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kohakuterrarium.builtins.plugins.sandbox import confine
from kohakuterrarium.builtins.plugins.sandbox import landlock
from kohakuterrarium.builtins.plugins.sandbox import plugin as plugin_mod
from kohakuterrarium.builtins.plugins.sandbox import roots
from kohakuterrarium.builtins.plugins.sandbox import seatbelt
from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.modules.plugin.base import PluginBlockError
from kohakuterrarium.modules.sandbox.presets import WORKSPACE

# macOS reports EPERM, Landlock EACCES.
DENIED = (b"Operation not permitted", b"Permission denied")


def _kernel_usable() -> bool:
    backend = confine.confinement_backend()
    if backend == confine.SEATBELT:
        probe = subprocess.run(
            [
                seatbelt.SANDBOX_EXEC,
                "-p",
                "(version 1)(allow default)",
                "/usr/bin/true",
            ],
            capture_output=True,
        )
        return probe.returncode == 0
    return backend is not None


needs_kernel = pytest.mark.skipif(
    not _kernel_usable(), reason="no usable kernel confinement on this host"
)
posix_paths = pytest.mark.skipif(
    sys.platform == "win32", reason="confinement backends use POSIX paths"
)


def _refused(result: dict) -> bool:
    return result["returncode"] != 0 and any(m in result["stderr"] for m in DENIED)


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """A workspace and a sibling the policy must not let a command write."""
    workspace = tmp_path / "workspace"
    outside = tmp_path / "home"
    workspace.mkdir()
    outside.mkdir()
    (outside / ".zshrc").write_text("export KEEP=1\n", encoding="utf-8")
    # pytest's tmp_path sits under the system temp root, which the profile
    # allows; drop the temp roots so the sibling is genuinely outside.
    monkeypatch.setattr(roots, "temp_roots", lambda: [])
    return workspace, outside


class TestBackendChoice:
    def test_seatbelt_wins_where_both_exist(self, monkeypatch):
        monkeypatch.setattr(seatbelt, "seatbelt_available", lambda: True)
        monkeypatch.setattr(landlock, "landlock_available", lambda: True)
        assert confine.confinement_backend() == confine.SEATBELT

    def test_landlock_where_seatbelt_is_missing(self, monkeypatch):
        monkeypatch.setattr(seatbelt, "seatbelt_available", lambda: False)
        monkeypatch.setattr(landlock, "landlock_available", lambda: True)
        assert confine.confinement_backend() == confine.LANDLOCK

    def test_none_without_either(self, monkeypatch):
        monkeypatch.setattr(seatbelt, "seatbelt_available", lambda: False)
        monkeypatch.setattr(landlock, "landlock_available", lambda: False)
        assert confine.confinement_backend() is None

    @posix_paths
    def test_seatbelt_argv_carries_the_profile(self, tmp_path):
        argv = confine.confine_argv(
            ["/bin/true"], confine.SEATBELT, WORKSPACE, tmp_path
        )
        assert argv[:2] == [seatbelt.SANDBOX_EXEC, "-p"]
        assert f'(subpath "{os.path.realpath(tmp_path)}")' in argv[2]
        assert argv[3:] == ["/bin/true"]

    @posix_paths
    def test_landlock_argv_carries_the_grants(self, tmp_path):
        argv = confine.confine_argv(
            ["/bin/true"], confine.LANDLOCK, WORKSPACE, tmp_path
        )
        assert argv[:4] == [sys.executable, "-I", "-S", str(landlock.LAUNCHER)]
        assert os.path.realpath(tmp_path) in json.loads(argv[4])
        assert argv[5:] == ["--", "/bin/true"]

    def test_unknown_backend_is_an_error(self, tmp_path):
        with pytest.raises(ValueError, match="unknown confinement backend"):
            confine.confine_argv(["/bin/true"], "jail", WORKSPACE, tmp_path)


class TestEnforceModes:
    @pytest.fixture
    def spawned(self, monkeypatch):
        calls: list[list[str]] = []

        async def fake_run(argv, **kwargs):
            calls.append(list(argv))
            return {"returncode": 0, "stdout": b"", "stderr": b"", "timed_out": False}

        monkeypatch.setattr(plugin_mod, "_run_plain_subprocess", fake_run)
        return calls

    async def test_require_refuses_to_spawn_without_a_kernel_sandbox(
        self, spawned, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(plugin_mod, "confinement_backend", lambda: None)
        with pytest.raises(PluginBlockError, match="kernel sandbox is unavailable"):
            await SandboxPlugin(os_enforce="require").run_subprocess_exec(
                ["/bin/true"], cwd=tmp_path
            )
        assert spawned == []

    async def test_auto_runs_unconfined_where_unsupported(
        self, spawned, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(plugin_mod, "confinement_backend", lambda: None)
        await SandboxPlugin().run_subprocess_exec(["/bin/true"], cwd=tmp_path)
        assert spawned == [["/bin/true"]]

    async def test_off_never_confines(self, spawned, monkeypatch, tmp_path):
        monkeypatch.setattr(plugin_mod, "confinement_backend", lambda: "seatbelt")
        await SandboxPlugin(os_enforce="off").run_subprocess_exec(
            ["/bin/true"], cwd=tmp_path
        )
        assert spawned == [["/bin/true"]]

    @posix_paths
    @pytest.mark.parametrize("backend", ["seatbelt", "landlock"])
    async def test_auto_confines_with_the_host_backend(
        self, spawned, monkeypatch, tmp_path, backend
    ):
        monkeypatch.setattr(plugin_mod, "confinement_backend", lambda: backend)
        await SandboxPlugin(fs_write_allow=[]).run_subprocess_exec(
            ["/bin/true"], cwd=tmp_path
        )
        argv = spawned[0]
        assert argv[-1] == "/bin/true"
        assert argv[0] == (
            seatbelt.SANDBOX_EXEC if backend == "seatbelt" else sys.executable
        )


@needs_kernel
class TestKernelEnforcement:
    async def test_rmtree_of_home_outside_the_workspace_is_refused(self, dirs):
        workspace, outside = dirs
        env = {**os.environ, "HOME": str(outside)}
        result = await SandboxPlugin().run_subprocess_exec(
            [
                sys.executable,
                "-c",
                "import os, shutil; shutil.rmtree(os.environ['HOME'])",
            ],
            cwd=workspace,
            env=env,
            timeout=30,
        )
        assert _refused(result), result
        assert (outside / ".zshrc").read_text(encoding="utf-8") == "export KEEP=1\n"

    async def test_writes_inside_the_workspace_still_work(self, dirs):
        workspace, _ = dirs
        result = await SandboxPlugin().run_subprocess_exec(
            ["/bin/sh", "-c", "mkdir -p build && echo ok > build/out.txt"],
            cwd=workspace,
            timeout=30,
        )
        assert result["returncode"] == 0, result["stderr"]
        assert (workspace / "build" / "out.txt").read_text(encoding="utf-8") == "ok\n"

    async def test_deny_list_holds_even_at_broad_write(self, dirs):
        workspace, outside = dirs
        sandbox = SandboxPlugin(fs_write="broad", fs_deny=[str(outside)])
        result = await sandbox.run_subprocess_exec(
            ["/bin/sh", "-c", f"rm -f '{outside}/.zshrc'"],
            cwd=workspace,
            timeout=30,
        )
        assert result["returncode"] != 0
        assert (outside / ".zshrc").exists()

    async def test_explicit_allow_carves_an_exception_out_of_the_deny_list(self, dirs):
        workspace, outside = dirs
        sockets = outside / "sockets"
        sockets.mkdir()
        sandbox = SandboxPlugin(fs_deny=[str(outside)], fs_write_allow=[str(sockets)])
        result = await sandbox.run_subprocess_exec(
            ["/bin/sh", "-c", f"touch '{sockets}/mux' && rm -f '{outside}/.zshrc'"],
            cwd=workspace,
            timeout=30,
        )
        assert (sockets / "mux").exists()
        assert result["returncode"] != 0
        assert (outside / ".zshrc").exists()

    async def test_renaming_a_parent_of_a_denied_path_is_refused(self, dirs):
        workspace, outside = dirs
        protected = outside / "config" / "git"
        protected.mkdir(parents=True)
        beside = outside / "config" / "other"
        beside.write_text("", encoding="utf-8")
        sandbox = SandboxPlugin(fs_write="broad", fs_deny=[str(protected)])
        result = await sandbox.run_subprocess_exec(
            ["/bin/mv", str(outside / "config"), str(outside / "moved")],
            cwd=workspace,
            timeout=30,
        )
        assert result["returncode"] != 0
        assert protected.is_dir()
        # Entries beside the denied path stay writable.
        appended = await sandbox.run_subprocess_exec(
            ["/bin/sh", "-c", f"echo x >> '{beside}'"],
            cwd=workspace,
            timeout=30,
        )
        assert appended["returncode"] == 0, appended["stderr"]
        assert beside.read_text(encoding="utf-8") == "x\n"

    async def test_grandchildren_inherit_the_confinement(self, dirs):
        workspace, outside = dirs
        result = await SandboxPlugin().run_subprocess_exec(
            ["/bin/sh", "-c", f"/bin/sh -c \"rm -f '{outside}/.zshrc'\""],
            cwd=workspace,
            timeout=30,
        )
        assert result["returncode"] != 0
        assert (outside / ".zshrc").exists()

    async def test_audit_backend_does_not_confine(self, dirs):
        workspace, outside = dirs
        result = await SandboxPlugin(backend="audit").run_subprocess_exec(
            ["/bin/sh", "-c", f"echo x > '{outside}/note'"],
            cwd=workspace,
            timeout=30,
        )
        assert result["returncode"] == 0
        assert (outside / "note").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
class TestProcessTree:
    async def _child_pid(self, pid_file: Path) -> int:
        for _ in range(200):
            if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip():
                return int(pid_file.read_text(encoding="utf-8"))
            await asyncio.sleep(0.01)
        raise AssertionError("child never started")

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    async def test_timeout_kills_the_whole_tree(self, tmp_path):
        pid_file = tmp_path / "child.pid"
        started = asyncio.get_running_loop().time()
        result = await SandboxPlugin(os_enforce="off").run_subprocess_exec(
            ["/bin/sh", "-c", f"sleep 60 & echo $! > '{pid_file}'; wait"],
            cwd=tmp_path,
            timeout=1,
        )
        # A surviving grandchild holds the pipes open, so a shell-only kill
        # returns only when the sleep itself ends.
        assert asyncio.get_running_loop().time() - started < 15
        assert result["timed_out"] is True
        child = int(pid_file.read_text(encoding="utf-8"))
        await asyncio.sleep(0.2)
        assert not self._alive(child)

    async def test_cancel_kills_the_whole_tree(self, tmp_path):
        pid_file = tmp_path / "child.pid"
        task = asyncio.create_task(
            SandboxPlugin(os_enforce="off").run_subprocess_exec(
                ["/bin/sh", "-c", f"sleep 60 & echo $! > '{pid_file}'; wait"],
                cwd=tmp_path,
            )
        )
        child = await self._child_pid(pid_file)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.2)
        assert not self._alive(child)
