"""Unit tests for Bash output-file lifecycle behavior."""

import asyncio

import pytest

from kohakuterrarium.builtins.tools import bash as bash_module
from kohakuterrarium.builtins.tools.bash import ShellTool
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.modules.tool.base import ToolConfig


def _capture_output_files(monkeypatch, tmp_path):
    paths = []

    def _create_output_file():
        path = tmp_path / f"bash_{len(paths)}.log"
        paths.append(path)
        return path, path.open("w+b")

    monkeypatch.setattr(bash_module, "_create_output_file", _create_output_file)
    return paths


class TestBashOutputFileLifecycle:
    @pytest.mark.skipif(
        bash_module._resolve_shell_executable("sh") is None,
        reason="POSIX shell is unavailable",
    )
    @pytest.mark.parametrize(
        ("command", "expected_output", "expected_error"),
        [
            ("printf success", "success", None),
            ("printf failure; exit 7", "failure", "Command exited with code 7"),
        ],
    )
    async def test_complete_outputs_are_removed_after_normalization(
        self, tmp_path, monkeypatch, command, expected_output, expected_error
    ):
        paths = _capture_output_files(monkeypatch, tmp_path)
        executor = Executor()
        executor._working_dir = tmp_path
        executor.register_tool(ShellTool(ToolConfig(timeout=1, max_output=1024)))

        result = await executor.wait_for(
            await executor.submit("bash", {"command": command, "type": "sh"})
        )

        assert result.output == expected_output
        assert result.error == expected_error
        assert "raw_output_path" not in result.metadata
        assert len(paths) == 1
        assert paths[0].exists() is False

    @pytest.mark.skipif(
        bash_module._resolve_shell_executable("sh") is None,
        reason="POSIX shell is unavailable",
    )
    async def test_complete_timeout_output_is_removed(self, tmp_path, monkeypatch):
        paths = _capture_output_files(monkeypatch, tmp_path)
        executor = Executor()
        executor._working_dir = tmp_path
        executor.register_tool(ShellTool(ToolConfig(timeout=0.05, max_output=1024)))

        result = await executor.wait_for(
            await executor.submit(
                "bash", {"command": "printf waiting; sleep 5", "type": "sh"}
            )
        )

        assert result.output == "waiting"
        assert "timed out" in result.error
        assert "raw_output_path" not in result.metadata
        assert len(paths) == 1
        assert paths[0].exists() is False

    @pytest.mark.skipif(
        bash_module._resolve_shell_executable("sh") is None,
        reason="POSIX shell is unavailable",
    )
    async def test_truncated_output_file_is_retained(self, tmp_path, monkeypatch):
        paths = _capture_output_files(monkeypatch, tmp_path)
        executor = Executor()
        executor._working_dir = tmp_path
        executor.register_tool(ShellTool(ToolConfig(timeout=1, max_output=5)))

        result = await executor.wait_for(
            await executor.submit(
                "bash", {"command": "printf 1234567890", "type": "sh"}
            )
        )

        assert result.metadata["truncated"] is True
        assert result.metadata["raw_output_path"] == str(paths[0])
        assert paths[0].read_text(encoding="utf-8") == "1234567890"
        paths[0].unlink()

    async def test_cancellation_removes_output_file(self, tmp_path, monkeypatch):
        output_path = tmp_path / "bash.log"
        process_started = asyncio.Event()
        process_terminated = asyncio.Event()

        class _FakeProcess:
            returncode = None

            async def wait(self):
                process_started.set()
                await asyncio.Event().wait()

        process = _FakeProcess()

        async def _create_subprocess(*_args, **_kwargs):
            return process

        async def _terminate(candidate):
            assert candidate is process
            process_terminated.set()

        def _create_output_file():
            return output_path, output_path.open("w+b")

        monkeypatch.setattr(
            bash_module, "_resolve_shell_executable", lambda _shell: "/fake/sh"
        )
        monkeypatch.setattr(
            bash_module.asyncio, "create_subprocess_exec", _create_subprocess
        )
        monkeypatch.setattr(bash_module, "terminate_process_tree", _terminate)
        monkeypatch.setattr(bash_module, "_create_output_file", _create_output_file)

        task = asyncio.create_task(
            ShellTool()._execute({"command": "long-running", "type": "sh"})
        )
        await asyncio.wait_for(process_started.wait(), timeout=1.0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert process_terminated.is_set()
        assert output_path.exists() is False

    @pytest.mark.parametrize(
        ("spawn_error", "expected_error"),
        [
            (FileNotFoundError, "Shell not found: /fake/sh"),
            (PermissionError, "Permission denied"),
        ],
    )
    async def test_spawn_failure_removes_output_file(
        self, tmp_path, monkeypatch, spawn_error, expected_error
    ):
        output_path = tmp_path / "bash.log"

        async def _create_subprocess(*_args, **_kwargs):
            raise spawn_error

        def _create_output_file():
            return output_path, output_path.open("w+b")

        monkeypatch.setattr(
            bash_module, "_resolve_shell_executable", lambda _shell: "/fake/sh"
        )
        monkeypatch.setattr(
            bash_module.asyncio, "create_subprocess_exec", _create_subprocess
        )
        monkeypatch.setattr(bash_module, "_create_output_file", _create_output_file)

        result = await ShellTool()._execute({"command": "echo", "type": "sh"})

        assert result.error == expected_error
        assert output_path.exists() is False
