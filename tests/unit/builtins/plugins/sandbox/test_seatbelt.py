"""Kernel write confinement of sandboxed subprocesses (macOS ``sandbox-exec``)."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kohakuterrarium.builtins.plugins.sandbox import plugin as plugin_mod
from kohakuterrarium.builtins.plugins.sandbox import seatbelt
from kohakuterrarium.builtins.plugins.sandbox.plugin import SandboxPlugin
from kohakuterrarium.modules.plugin.base import PluginBlockError
from kohakuterrarium.modules.sandbox.presets import WORKSPACE


def _seatbelt_usable() -> bool:
    if not seatbelt.seatbelt_available():
        return False
    probe = subprocess.run(
        [seatbelt.SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"],
        capture_output=True,
    )
    return probe.returncode == 0


needs_seatbelt = pytest.mark.skipif(
    not _seatbelt_usable(), reason="sandbox-exec is unavailable or nested"
)


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
    monkeypatch.setattr(seatbelt, "temp_roots", lambda: [])
    return workspace, outside


class TestBuildWriteProfile:
    def test_workspace_level_allows_only_listed_roots(self, tmp_path):
        text = seatbelt.build_write_profile(
            WORKSPACE, tmp_path, write_allow=["~/.cache"]
        )
        lines = text.splitlines()
        assert lines[:3] == ["(version 1)", "(allow default)", "(deny file-write*)"]
        allow, deny, exceptions = lines[3:]
        assert allow.startswith("(allow file-write*")
        assert f'(subpath "{os.path.realpath(tmp_path)}")' in allow
        assert '(subpath "/dev")' in allow
        # The deny list overrides the workspace, then explicit allows override it.
        assert deny.startswith("(deny file-write*")
        assert f'(subpath "{Path("~/.ssh").expanduser()}")' in deny
        assert exceptions == (
            f'(allow file-write* (subpath "{Path("~/.cache").expanduser()}"))'
        )

    def test_broad_level_keeps_only_the_deny_list(self, tmp_path):
        text = seatbelt.build_write_profile(
            WORKSPACE.with_overrides(fs_write="broad"), tmp_path, write_allow=[]
        )
        assert "(deny file-write*)\n" not in text
        assert "(allow file-write*" not in text
        assert f'(subpath "{Path("~/.zshrc").expanduser()}")' in text

    def test_deny_level_allows_devices_only(self, tmp_path):
        text = seatbelt.build_write_profile(
            WORKSPACE.with_overrides(fs_write="deny"), tmp_path
        )
        assert '(allow file-write* (subpath "/dev"))' in text
        assert str(tmp_path) not in text
        assert ".cache" not in text

    def test_paths_are_quoted_for_the_profile_language(self, tmp_path):
        odd = tmp_path / 'we"ird'
        text = seatbelt.build_write_profile(WORKSPACE, odd, write_allow=[])
        assert '(subpath "' + str(odd).replace('"', '\\"') + '")' in text


@needs_seatbelt
class TestKernelEnforcement:
    async def test_rmtree_of_home_outside_the_workspace_is_refused(self, dirs):
        workspace, outside = dirs
        sandbox = SandboxPlugin()
        env = {**os.environ, "HOME": str(outside)}

        result = await sandbox.run_subprocess_exec(
            [
                sys.executable,
                "-c",
                "import os, shutil; shutil.rmtree(os.environ['HOME'])",
            ],
            cwd=workspace,
            env=env,
            timeout=30,
        )

        assert result["returncode"] != 0
        assert b"Operation not permitted" in result["stderr"]
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

    async def test_audit_backend_does_not_confine(self, dirs):
        workspace, outside = dirs
        result = await SandboxPlugin(backend="audit").run_subprocess_exec(
            ["/bin/sh", "-c", f"echo x > '{outside}/note'"],
            cwd=workspace,
            timeout=30,
        )
        assert result["returncode"] == 0
        assert (outside / "note").exists()


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
        monkeypatch.setattr(plugin_mod, "seatbelt_available", lambda: False)
        with pytest.raises(PluginBlockError, match="kernel sandbox is unavailable"):
            await SandboxPlugin(os_enforce="require").run_subprocess_exec(
                ["/bin/true"], cwd=tmp_path
            )
        assert spawned == []

    async def test_auto_runs_unconfined_where_unsupported(
        self, spawned, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(plugin_mod, "seatbelt_available", lambda: False)
        await SandboxPlugin().run_subprocess_exec(["/bin/true"], cwd=tmp_path)
        assert spawned == [["/bin/true"]]

    async def test_off_never_confines(self, spawned, monkeypatch, tmp_path):
        monkeypatch.setattr(plugin_mod, "seatbelt_available", lambda: True)
        await SandboxPlugin(os_enforce="off").run_subprocess_exec(
            ["/bin/true"], cwd=tmp_path
        )
        assert spawned == [["/bin/true"]]

    async def test_auto_confines_where_supported(self, spawned, monkeypatch, tmp_path):
        monkeypatch.setattr(plugin_mod, "seatbelt_available", lambda: True)
        await SandboxPlugin(fs_write_allow=[]).run_subprocess_exec(
            ["/bin/true"], cwd=tmp_path
        )
        argv = spawned[0]
        assert argv[:2] == [seatbelt.SANDBOX_EXEC, "-p"]
        assert f'(subpath "{os.path.realpath(tmp_path)}")' in argv[2]
        assert argv[3:] == ["/bin/true"]


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
