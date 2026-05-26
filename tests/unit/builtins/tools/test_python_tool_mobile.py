"""Unit tests for ``PythonTool`` on the mobile profile.

The python tool's default path spawns ``sys.executable -c <code>``
as a subprocess.  That fails on Android because the Chaquopy
runtime can't re-init itself from a subprocess (dalvik-cache
ownership EACCES).  On ``KT_PROFILE=mobile`` the tool drops into
:meth:`PythonTool._execute_in_process` which runs the code inside
the host interpreter via ``exec()``, captures stdout / stderr, and
honours timeout via ``asyncio.to_thread``.

These tests pin the in-process path's contract end-to-end against a
real ``PythonTool`` instance — no mocks of stdlib subprocess.  The
tool is constructed via the builtin registry so its dependency on
config / context surfaces too.
"""

import asyncio
import os

import pytest

from kohakuterrarium.builtins.tools.python import PythonTool
from kohakuterrarium.modules.tool.base import ToolConfig


class _Ctx:
    """Minimal context shim — ``PythonTool.needs_context=True``
    means it'll get a ``context=...`` kwarg, but for in-process exec
    we only need ``working_dir`` (or no attribute at all → falls back
    to ``self.config.working_dir or os.getcwd()``)."""

    def __init__(self, working_dir: str | None = None):
        self.working_dir = working_dir
        self.runtime_services: dict = {}


@pytest.fixture(autouse=True)
def _force_mobile(monkeypatch):
    monkeypatch.setenv("KT_PROFILE", "mobile")
    yield


def _make_tool() -> PythonTool:
    tool = PythonTool()
    tool.config = ToolConfig()
    return tool


class TestPythonToolInProcessOnMobile:
    @pytest.mark.asyncio
    async def test_captures_stdout(self):
        tool = _make_tool()
        result = await tool._execute(
            {"code": "print('hello from in-process')"}, context=_Ctx()
        )
        assert result.error is None
        assert result.exit_code == 0
        assert "hello from in-process" in result.output
        # Pins that we took the in-process branch — the subprocess
        # path metadata would have ``"runtime"`` absent.
        assert result.metadata.get("runtime") == "in_process"

    @pytest.mark.asyncio
    async def test_captures_stderr(self):
        tool = _make_tool()
        result = await tool._execute(
            {"code": ("import sys; print('out'); print('err', file=sys.stderr)")},
            context=_Ctx(),
        )
        assert result.exit_code == 0
        assert "out" in result.output
        assert "err" in result.output

    @pytest.mark.asyncio
    async def test_system_exit_zero_is_success(self):
        tool = _make_tool()
        result = await tool._execute(
            {"code": "import sys; sys.exit(0)"}, context=_Ctx()
        )
        assert result.exit_code == 0
        assert result.error is None

    @pytest.mark.asyncio
    async def test_system_exit_nonzero_propagates(self):
        tool = _make_tool()
        result = await tool._execute(
            {"code": "import sys; sys.exit(3)"}, context=_Ctx()
        )
        assert result.exit_code == 3

    @pytest.mark.asyncio
    async def test_exception_returns_traceback(self):
        tool = _make_tool()
        result = await tool._execute(
            {"code": "raise RuntimeError('boom')"}, context=_Ctx()
        )
        # In-process exec wraps every BaseException — exit_code 1 +
        # traceback in output, plain ``error`` string for the agent.
        assert result.exit_code == 1
        assert "RuntimeError" in result.output
        assert "boom" in result.output
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_namespace_is_isolated_per_call(self):
        # Each call gets its own ``namespace`` dict; module-level
        # state set in one call must NOT leak into the next.
        tool = _make_tool()
        first = await tool._execute({"code": "x = 1; print(x)"}, context=_Ctx())
        second = await tool._execute({"code": "print('x' in dir())"}, context=_Ctx())
        assert "1" in first.output
        assert "False" in second.output

    @pytest.mark.asyncio
    async def test_no_code_returns_error(self):
        tool = _make_tool()
        result = await tool._execute({"code": ""}, context=_Ctx())
        assert result.error is not None
        # Doesn't reach the exec branch — ``runtime`` metadata absent.
        assert result.metadata is None or "runtime" not in (result.metadata or {})

    @pytest.mark.asyncio
    async def test_cwd_is_restored_after_call(self, tmp_path):
        # The in-process exec chdirs to the context's working_dir
        # for the duration of the call.  After the call, the
        # process-wide cwd must be back to what it was — otherwise
        # later tools / requests see a stale cwd.
        original_cwd = os.getcwd()
        tool = _make_tool()
        result = await tool._execute(
            {"code": "import os; print(os.getcwd())"},
            context=_Ctx(working_dir=str(tmp_path)),
        )
        assert result.exit_code == 0
        assert str(tmp_path) in result.output
        assert os.getcwd() == original_cwd

    @pytest.mark.asyncio
    async def test_does_not_spawn_subprocess(self, monkeypatch):
        # Hard guard: on mobile profile we MUST NOT spawn a
        # subprocess — that's what triggers the dalvik-cache
        # EACCES the in-process path exists to dodge.  Monkey-patch
        # ``asyncio.create_subprocess_exec`` to blow up if the tool
        # accidentally falls through to the subprocess path.
        async def _explode(*args, **kwargs):
            raise AssertionError(
                "python tool spawned a subprocess on mobile profile; "
                "expected in-process exec() path"
            )

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _explode)
        tool = _make_tool()
        result = await tool._execute({"code": "print('ok')"}, context=_Ctx())
        assert result.exit_code == 0
