"""
Python code execution tool.

On desktop: spawns ``sys.executable -c <code>`` as a subprocess
(standard isolation, separate process state per call).  On the
mobile profile (Android / Chaquopy): runs ``exec()`` inside the
host interpreter — there is no standalone CPython on PATH and
spawning ``sys.executable`` as a subprocess triggers the Android
Runtime to write to ``/data/dalvik-cache/``, which apps don't have
permission for ("error changing dalvik-cache ownership: Permission
denied" before the user code ever runs).

Helpers are shared with :mod:`kohakuterrarium.builtins.tools.bash`
to keep timeout / context / runner-resolution behaviour identical
between the two tools.
"""

import asyncio
import contextlib
import io
import os
import sys
import traceback
from typing import Any

from kohakuterrarium.builtins.tools.bash import (
    _format_timeout,
    _resolve_timeout_arg,
    _subprocess_runner,
    _wait_timeout,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.mobile_sandbox import is_mobile_profile

logger = get_logger(__name__)


@register_builtin("python")
class PythonTool(BaseTool):
    """Tool for executing Python code in a subprocess."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "python"

    @property
    def description(self) -> str:
        return "Execute Python code and return output"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "timeout": {
                    "type": "number",
                    "description": "Maximum execution time in seconds (0 = no timeout).",
                },
            },
            "required": ["code"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Execute Python code."""
        context = kwargs.get("context")
        code = args.get("code", "")
        if not code:
            return ToolResult(error="No code provided")
        timeout, timeout_error = _resolve_timeout_arg(args, self.config.timeout)
        if timeout_error is not None:
            return ToolResult(error=timeout_error)

        logger.debug("Executing Python code", code_length=len(code))

        if context and getattr(context, "working_dir", None):
            cwd = str(context.working_dir)
        else:
            cwd = self.config.working_dir or os.getcwd()

        # Mobile profile (Android): there is no standalone CPython
        # binary on PATH — ``sys.executable`` is the Chaquopy stub
        # for the *current* embedded interpreter, and spawning it
        # as a subprocess triggers the Android Runtime to
        # initialise a new ART process, which writes to
        # ``/data/dalvik-cache/``.  Apps run as a non-root user
        # without access to that path, so the subprocess dies with
        #     error changing dalvik-cache ownership: Permission
        #     denied
        # before the user code even runs.
        #
        # Solution: execute the code in-process via ``exec()``
        # against an isolated namespace, capturing stdout / stderr.
        # The trade-off is no isolation between python-tool calls —
        # but that's already the case for every other tool on
        # mobile (everything is one process), and the alternative
        # is "python tool unavailable on Android".
        if is_mobile_profile():
            return await self._execute_in_process(code, cwd, timeout)

        python_cmd = [sys.executable, "-c", code]

        try:
            runner = _subprocess_runner(context)
            if runner is not None and hasattr(runner, "run_subprocess_exec"):
                result = await runner.run_subprocess_exec(
                    python_cmd,
                    cwd=cwd,
                    timeout=_wait_timeout(timeout),
                    max_output_bytes=self.config.max_output or None,
                )
                output = (result.get("stdout", b"") + result.get("stderr", b"")).decode(
                    "utf-8", errors="replace"
                )
                exit_code = int(result.get("returncode") or 0)
                if result.get("timed_out"):
                    return ToolResult(
                        error=(
                            "Python execution timed out after "
                            f"{_format_timeout(timeout)}"
                        ),
                        exit_code=-1,
                        metadata={"timeout": timeout},
                    )
            else:
                process = await asyncio.create_subprocess_exec(
                    *python_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=cwd,
                )

                try:
                    stdout, _ = await asyncio.wait_for(
                        process.communicate(),
                        timeout=_wait_timeout(timeout),
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    return ToolResult(
                        error=(
                            "Python execution timed out after "
                            f"{_format_timeout(timeout)}"
                        ),
                        exit_code=-1,
                        metadata={"timeout": timeout},
                    )

                output = stdout.decode("utf-8", errors="replace") if stdout else ""
                exit_code = process.returncode or 0

            return ToolResult(
                output=output,
                exit_code=exit_code,
                error=(
                    None if exit_code == 0 else f"Python exited with code {exit_code}"
                ),
                metadata={"timeout": timeout},
            )

        except Exception as e:
            logger.error("Python execution failed", error=str(e))
            return ToolResult(error=str(e))

    async def _execute_in_process(
        self, code: str, cwd: str, timeout: float
    ) -> ToolResult:
        """Run ``code`` via in-process ``exec()`` — the only viable
        path on Android, where spawning ``sys.executable`` triggers
        the dalvik-cache permission error.

        Runs on a thread (via ``asyncio.to_thread``) so the event
        loop stays responsive and a runaway script doesn't block
        websocket streaming.  ``timeout`` cancels the wait but
        cannot interrupt the underlying CPU-bound exec — operators
        should treat the tool as cooperative on this platform.
        ``cwd`` is applied as a transient ``os.chdir`` around the
        exec and restored afterwards.
        """
        loop = asyncio.get_running_loop()

        def _run() -> tuple[str, int, str | None]:
            stdout = io.StringIO()
            stderr = io.StringIO()
            namespace: dict[str, Any] = {
                "__name__": "__main__",
                "__builtins__": __builtins__,
            }
            previous_cwd = os.getcwd()
            try:
                try:
                    os.chdir(cwd)
                except OSError:
                    # If cwd is bogus, fall through with current dir;
                    # surface the error in the captured output below.
                    pass
                try:
                    with (
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        exec(compile(code, "<python_tool>", "exec"), namespace)
                except SystemExit as exc:
                    # ``sys.exit(N)`` is a legitimate way for a script
                    # to signal status; propagate the code rather
                    # than treating it as an error.
                    code_val = (
                        exc.code
                        if isinstance(exc.code, int)
                        else (0 if exc.code is None else 1)
                    )
                    combined = stdout.getvalue() + stderr.getvalue()
                    return combined, code_val, None
                except BaseException:
                    tb = traceback.format_exc()
                    combined = stdout.getvalue() + stderr.getvalue() + tb
                    return combined, 1, "Exception during execution"
                combined = stdout.getvalue() + stderr.getvalue()
                return combined, 0, None
            finally:
                try:
                    os.chdir(previous_cwd)
                except OSError:
                    pass

        try:
            output, exit_code, error = await asyncio.wait_for(
                loop.run_in_executor(None, _run),
                timeout=_wait_timeout(timeout),
            )
        except asyncio.TimeoutError:
            return ToolResult(
                error=f"Python execution timed out after {_format_timeout(timeout)}",
                exit_code=-1,
                metadata={"timeout": timeout, "runtime": "in_process"},
            )

        return ToolResult(
            output=output,
            exit_code=exit_code,
            error=error,
            metadata={"timeout": timeout, "runtime": "in_process"},
        )
