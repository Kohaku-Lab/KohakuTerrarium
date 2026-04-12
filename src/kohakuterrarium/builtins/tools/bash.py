"""
Shell command execution tool.

Executes commands via a specified shell (bash, zsh, sh, etc.).
On all platforms, prefers bash (git bash available on Windows).
"""

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolConfig,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Shell type → (executable, args-before-command)
# The command string is appended after these args.
_SHELL_SPECS: dict[str, tuple[str, list[str]]] = {
    "bash": ("bash", ["-c"]),
    "zsh": ("zsh", ["-c"]),
    "sh": ("sh", ["-c"]),
    "fish": ("fish", ["-c"]),
    "pwsh": ("pwsh", ["-NoProfile", "-NonInteractive", "-Command"]),
    "powershell": (
        "powershell",
        ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"],
    ),
}

_AVAILABLE_SHELLS: list[str] | None = None


def _shell_override_env(shell_type: str) -> str | None:
    specific = os.environ.get(f"KT_{shell_type.upper()}_PATH")
    if specific:
        return specific
    generic = os.environ.get("KT_SHELL_PATH")
    if generic:
        return generic
    if shell_type in {"bash", "zsh", "sh"}:
        env_shell = os.environ.get("SHELL")
        if env_shell and any(
            name in env_shell.lower() for name in ("bash", "zsh", "sh")
        ):
            return env_shell
    return None


def _windows_git_bash_candidates() -> list[str]:
    candidates: list[str] = []
    program_files = [
        os.environ.get("ProgramW6432"),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")

    for base in [p for p in program_files if p]:
        candidates.extend(
            [
                str(Path(base) / "Git" / "bin" / "bash.exe"),
                str(Path(base) / "Git" / "usr" / "bin" / "bash.exe"),
            ]
        )
    if local_app_data:
        candidates.append(
            str(Path(local_app_data) / "Programs" / "Git" / "bin" / "bash.exe")
        )
    if home:
        candidates.extend(
            [
                str(
                    Path(home)
                    / "AppData"
                    / "Local"
                    / "Programs"
                    / "Git"
                    / "bin"
                    / "bash.exe"
                ),
                str(
                    Path(home)
                    / "scoop"
                    / "apps"
                    / "git"
                    / "current"
                    / "bin"
                    / "bash.exe"
                ),
            ]
        )

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _resolve_shell_executable(shell_type: str) -> str | None:
    override = _shell_override_env(shell_type)
    if override and shutil.which(override):
        return shutil.which(override)
    if override and Path(override).exists():
        return override

    exe = _SHELL_SPECS[shell_type][0]
    if sys.platform == "win32" and shell_type == "bash":
        for candidate in _windows_git_bash_candidates():
            if Path(candidate).exists():
                return candidate
    return shutil.which(exe)


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate a subprocess and its children best-effort."""
    try:
        if process.returncode is not None:
            return

        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except Exception:
                process.terminate()

            try:
                await asyncio.wait_for(process.wait(), timeout=3)
                return
            except asyncio.TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    return
                except Exception:
                    process.kill()

        await asyncio.wait_for(process.wait(), timeout=5)
    except ProcessLookupError:
        pass
    except Exception:
        try:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)
        except Exception:
            pass


def _create_output_file() -> tuple[Path, Any]:
    output_dir = Path(tempfile.gettempdir()) / "kohakuterrarium-bash"
    output_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w+b",
        prefix="bash_",
        suffix=".log",
        dir=output_dir,
        delete=False,
    )
    return Path(handle.name), handle


def _read_output_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _truncate_output(output: str, max_output: int) -> str:
    if max_output > 0 and len(output) > max_output:
        return (
            output[:max_output]
            + f"\n... (truncated, {len(output) - max_output} chars omitted)"
        )
    return output


def _get_available_shells() -> list[str]:
    """Return shell types whose executable can be resolved (cached)."""
    global _AVAILABLE_SHELLS
    if _AVAILABLE_SHELLS is None:
        _AVAILABLE_SHELLS = [
            name for name in _SHELL_SPECS if _resolve_shell_executable(name)
        ]
    return _AVAILABLE_SHELLS


@register_builtin("bash")
class ShellTool(BaseTool):
    """
    Tool for executing shell commands.

    Supports multiple shell types via the ``type`` parameter.
    Defaults to bash on all platforms (git bash on Windows).
    """

    needs_context = True

    def __init__(self, config: ToolConfig | None = None):
        super().__init__(config)

    @property
    def tool_name(self) -> str:
        return "bash"

    @property
    def description(self) -> str:
        return "Execute shell commands (prefer dedicated tools for file ops)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "type": {
                    "type": "string",
                    "description": (
                        "Shell type (default: bash). "
                        "Options: bash, zsh, sh, fish, pwsh, powershell"
                    ),
                },
            },
            "required": ["command"],
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Execute the command."""
        context = kwargs.get("context")
        command = args.get("command", "")
        if not command:
            return ToolResult(error="No command provided")

        # Reject no-op waiting commands (hallucination pattern)
        stripped = command.strip().lower()
        if stripped.startswith("echo") and any(
            w in stripped
            for w in ("waiting", "wait for", "still running", "in progress")
        ):
            return ToolResult(
                error="Do not use bash to fake-wait for background tasks. "
                "Background results arrive automatically. "
                "Just stop your response — do not echo/sleep/poll."
            )
        if stripped.startswith("sleep"):
            return ToolResult(
                error="Do not sleep to wait for background tasks. "
                "Results arrive automatically when ready. "
                "Just stop your response."
            )

        # Resolve shell type
        shell_type = args.get("type", "bash").lower().strip()
        if shell_type not in _SHELL_SPECS:
            available = _get_available_shells()
            return ToolResult(
                error=f"Unknown shell type: {shell_type}. "
                f"Available: {', '.join(available) or 'none found'}"
            )

        spec_exe, prefix_args = _SHELL_SPECS[shell_type]
        resolved_exe = _resolve_shell_executable(shell_type)
        if not resolved_exe:
            available = _get_available_shells()
            return ToolResult(
                error=(
                    f"Shell '{shell_type}' ({spec_exe}) not found. "
                    f"Available shells: {', '.join(available) or 'none found'}. "
                    f'Try: bash(type="{available[0]}", ...)'
                    if available
                    else "No shells found."
                )
            )

        full_command = [resolved_exe, *prefix_args, command]

        logger.debug("Executing command", shell=shell_type, command=command[:100])

        # Set up environment
        env = os.environ.copy()
        if self.config.env:
            env.update(self.config.env)

        # Set working directory: context (agent-aware) > tool config > process cwd
        if context and getattr(context, "working_dir", None):
            cwd = str(context.working_dir)
        else:
            cwd = self.config.working_dir or os.getcwd()

        process = None
        output_path = None
        output_handle = None
        try:
            output_path, output_handle = _create_output_file()

            popen_kwargs: dict[str, Any] = {
                "stdout": output_handle,
                "stderr": subprocess.STDOUT,
                "cwd": cwd,
                "env": env,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True

            process = await asyncio.create_subprocess_exec(
                *full_command,
                **popen_kwargs,
            )

            if output_handle is not None:
                output_handle.close()
                output_handle = None

            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self.config.timeout if self.config.timeout > 0 else None,
                )
            except asyncio.TimeoutError:
                await _terminate_process_tree(process)
                output = _truncate_output(
                    _read_output_file(output_path), self.config.max_output
                )
                return ToolResult(
                    output=output,
                    error=f"Command timed out after {self.config.timeout}s",
                    exit_code=-1,
                    metadata={
                        "raw_output_path": str(output_path),
                        "shell_type": shell_type,
                        "shell_executable": resolved_exe,
                        "shell_override": _shell_override_env(shell_type),
                        "home": env.get("HOME"),
                    },
                )
            except asyncio.CancelledError:
                await _terminate_process_tree(process)
                raise

            output = _truncate_output(
                _read_output_file(output_path), self.config.max_output
            )
            exit_code = process.returncode or 0

            logger.debug(
                "Command completed",
                exit_code=exit_code,
                output_length=len(output),
            )

            return ToolResult(
                output=output,
                exit_code=exit_code,
                error=(
                    None if exit_code == 0 else f"Command exited with code {exit_code}"
                ),
                metadata={
                    "raw_output_path": str(output_path),
                    "shell_type": shell_type,
                    "shell_executable": resolved_exe,
                    "shell_override": _shell_override_env(shell_type),
                    "home": env.get("HOME"),
                },
            )

        except FileNotFoundError:
            return ToolResult(error=f"Shell not found: {resolved_exe or spec_exe}")
        except PermissionError:
            return ToolResult(error="Permission denied")
        except asyncio.CancelledError:
            logger.info("Command cancelled", shell=shell_type, command=command[:100])
            raise
        except Exception as e:
            logger.error("Command execution failed", error=str(e))
            if process is not None:
                await _terminate_process_tree(process)
            error_output = (
                _truncate_output(_read_output_file(output_path), self.config.max_output)
                if output_path and output_path.exists()
                else ""
            )
            return ToolResult(
                output=error_output,
                error=str(e),
                metadata=(
                    {
                        "raw_output_path": str(output_path),
                        "shell_type": shell_type,
                        "shell_executable": resolved_exe,
                        "shell_override": _shell_override_env(shell_type),
                        "home": env.get("HOME"),
                    }
                    if output_path
                    else {}
                ),
            )
        finally:
            if output_handle is not None:
                output_handle.close()


# Backward-compatible alias
BashTool = ShellTool


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

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Execute Python code."""
        context = kwargs.get("context")
        code = args.get("code", "")
        if not code:
            return ToolResult(error="No code provided")

        logger.debug("Executing Python code", code_length=len(code))

        python_cmd = [sys.executable, "-c", code]
        if context and getattr(context, "working_dir", None):
            cwd = str(context.working_dir)
        else:
            cwd = self.config.working_dir or os.getcwd()

        try:
            process = await asyncio.create_subprocess_exec(
                *python_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )

            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.config.timeout if self.config.timeout > 0 else None,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(
                    error=f"Python execution timed out after {self.config.timeout}s",
                    exit_code=-1,
                )

            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            exit_code = process.returncode or 0

            return ToolResult(
                output=output,
                exit_code=exit_code,
                error=(
                    None if exit_code == 0 else f"Python exited with code {exit_code}"
                ),
            )

        except Exception as e:
            logger.error("Python execution failed", error=str(e))
            return ToolResult(error=str(e))
