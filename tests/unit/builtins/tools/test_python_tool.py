"""Unit tests for ``PythonTool``'s default subprocess execution path."""

import asyncio

import pytest

from kohakuterrarium.builtins.tools import python as python_mod
from kohakuterrarium.builtins.tools.python import PythonTool
from kohakuterrarium.modules.tool.base import ToolConfig


def _make_tool() -> PythonTool:
    tool = PythonTool()
    tool.config = ToolConfig()
    return tool


class TestPythonToolSubprocess:
    @pytest.mark.asyncio
    async def test_runs_code_and_captures_output(self):
        tool = _make_tool()

        result = await tool._execute({"code": "print(2**16)"})

        assert result.error is None
        assert result.exit_code == 0
        assert "65536" in result.output

    @pytest.mark.asyncio
    async def test_cancel_kills_the_spawned_interpreter(self, monkeypatch):
        spawned: list[asyncio.subprocess.Process] = []
        real_exec = asyncio.create_subprocess_exec

        async def recording_exec(*args, **kwargs):
            process = await real_exec(*args, **kwargs)
            spawned.append(process)
            return process

        monkeypatch.setattr(
            python_mod.asyncio, "create_subprocess_exec", recording_exec
        )
        tool = _make_tool()

        task = asyncio.create_task(
            tool._execute({"code": "import time; time.sleep(60)"})
        )
        for _ in range(500):
            if spawned:
                break
            await asyncio.sleep(0.01)
        assert spawned, "subprocess was never spawned"

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The cancel handler must reap the child before re-raising.
        assert spawned[0].returncode is not None
