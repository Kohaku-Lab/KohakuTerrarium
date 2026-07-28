"""Unit tests for ``builtins/tools/subprocess/shell_utils.py``."""

import asyncio
import sys

import pytest

from kohakuterrarium.builtins.tools.subprocess import shell_utils


class TestWindowsProcessKwargs:
    def test_hides_shell_window_on_windows(self, monkeypatch):
        class FakeStartupInfo:
            def __init__(self):
                self.dwFlags = 0
                self.wShowWindow = None

        monkeypatch.setattr(shell_utils.sys, "platform", "win32")
        monkeypatch.setattr(
            shell_utils.subprocess, "STARTUPINFO", FakeStartupInfo, raising=False
        )
        monkeypatch.setattr(
            shell_utils.subprocess, "STARTF_USESHOWWINDOW", 0x01, raising=False
        )
        monkeypatch.setattr(shell_utils.subprocess, "SW_HIDE", 0, raising=False)
        monkeypatch.setattr(
            shell_utils.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False
        )
        monkeypatch.setattr(
            shell_utils.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
        )

        kwargs = shell_utils.windows_process_kwargs()

        assert (
            kwargs["startupinfo"].dwFlags & shell_utils.subprocess.STARTF_USESHOWWINDOW
        )
        assert kwargs["startupinfo"].wShowWindow == shell_utils.subprocess.SW_HIDE
        assert kwargs["creationflags"] & shell_utils.subprocess.CREATE_NEW_PROCESS_GROUP
        assert kwargs["creationflags"] & shell_utils.subprocess.CREATE_NO_WINDOW

    def test_empty_off_windows(self, monkeypatch):
        monkeypatch.setattr(shell_utils.sys, "platform", "linux")

        assert shell_utils.windows_process_kwargs() == {}

    def test_kwargs_spawn_a_working_hidden_process(self):
        # The returned options must be directly consumable by asyncio's
        # subprocess spawn on the running platform (real spawn, no stubs).
        async def scenario() -> int:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "pass",
                **shell_utils.windows_process_kwargs(),
            )
            return await process.wait()

        assert asyncio.run(scenario()) == 0


class TestTerminateProcessTree:
    def test_kills_a_running_child(self):
        async def scenario() -> int | None:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
                **shell_utils.windows_process_kwargs(),
            )
            await shell_utils.terminate_process_tree(process)
            return process.returncode

        returncode = asyncio.run(scenario())
        assert returncode is not None
        assert returncode != 0

    def test_finished_child_is_left_alone(self):
        async def scenario() -> int | None:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "pass",
            )
            await process.wait()
            before = process.returncode
            await shell_utils.terminate_process_tree(process)
            assert process.returncode == before
            return before

        assert asyncio.run(scenario()) == 0

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX process-group escalation path"
    )
    def test_kills_child_process_group_on_posix(self):
        async def scenario() -> int | None:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
                start_new_session=True,
            )
            await shell_utils.terminate_process_tree(process)
            return process.returncode

        assert asyncio.run(scenario()) is not None

    def test_non_group_child_escalates_to_direct_kill(self, monkeypatch):
        class FakeProcess:
            pid = 1234
            returncode = None
            terminated = False
            killed = False

            async def wait(self):
                return self.returncode

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True
                self.returncode = -9

        process = FakeProcess()
        group_signals = []
        wait_timeouts = []

        def missing_group(pid, sig):
            assert pid == process.pid
            group_signals.append(sig)
            raise ProcessLookupError

        async def controlled_wait(awaitable, timeout):
            wait_timeouts.append(timeout)
            if len(wait_timeouts) == 1:
                awaitable.close()
                raise asyncio.TimeoutError
            return await awaitable

        monkeypatch.setattr(shell_utils.sys, "platform", "linux")
        monkeypatch.setattr(shell_utils.os, "killpg", missing_group, raising=False)
        monkeypatch.setattr(shell_utils.signal, "SIGKILL", 9, raising=False)
        monkeypatch.setattr(shell_utils.asyncio, "wait_for", controlled_wait)

        asyncio.run(shell_utils.terminate_process_tree(process))

        assert group_signals == [shell_utils.signal.SIGTERM, 9]
        assert wait_timeouts == [3, 5]
        assert process.terminated is True
        assert process.killed is True
        assert process.returncode == -9
