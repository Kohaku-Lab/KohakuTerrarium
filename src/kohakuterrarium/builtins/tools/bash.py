"""Cross-platform shell command execution with bounded output and timeouts."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.tools.bash_windows import windows_git_bash_candidates
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.builtins.tools.subprocess.shell_utils import (
    terminate_process_tree,
    windows_process_kwargs,
)
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolConfig,
    ToolResult,
)
from kohakuterrarium.utils.logging import get_logger
from kohakuterrarium.utils.mobile_sandbox import (
    bundled_sh_command,
    is_mobile_profile,
    sandbox_bin_dir,
    sandbox_binary,
)

logger = get_logger(__name__)

# Each prefix is followed by the caller's command string.
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


def _shell_override_name(shell_type: str) -> str | None:
    """Return the environment variable providing the active shell override."""
    specific_name = f"KT_{shell_type.upper()}_PATH"
    if os.environ.get(specific_name):
        return specific_name
    if os.environ.get("KT_SHELL_PATH"):
        return "KT_SHELL_PATH"
    return None


def _shell_override_env(shell_type: str) -> str | None:
    override_name = _shell_override_name(shell_type)
    return os.environ.get(override_name) if override_name else None


def _resolve_override_executable(override: str | None) -> str | None:
    """Resolve an explicit shell override without selecting a fallback."""
    if not override:
        return None
    resolved = shutil.which(override)
    if resolved:
        return resolved
    if Path(override).exists():
        return override
    return None


def _resolve_shell_executable(shell_type: str) -> str | None:
    # Android's executable system shell is preferred over app-data binaries,
    # which may be blocked by platform W^X or noexec policy. Bash-like requests
    # share this POSIX shell because stock Android does not ship those binaries.
    if is_mobile_profile() and shell_type in {"bash", "sh", "zsh"}:
        system_sh = Path("/system/bin/sh")
        if system_sh.is_file():
            return str(system_sh)
        bundled = sandbox_binary("sh")
        if bundled is not None:
            return str(bundled)

    override = _resolve_override_executable(_shell_override_env(shell_type))
    if override:
        return override

    exe = _SHELL_SPECS[shell_type][0]
    if sys.platform == "win32" and shell_type == "bash":
        for candidate in windows_git_bash_candidates():
            if Path(candidate).exists():
                return candidate
    return shutil.which(exe)


def _build_shell_argv(shell_type: str, resolved_exe: str, command: str) -> list[str]:
    """Compose shell arguments, preserving BusyBox multicall dispatch."""
    if (
        is_mobile_profile()
        and shell_type in {"bash", "sh", "zsh"}
        and _is_bundled_busybox(resolved_exe)
    ):
        bundled = bundled_sh_command(command)
        if bundled is not None:
            return bundled
    prefix_args = _SHELL_SPECS[shell_type][1]
    return [resolved_exe, *prefix_args, command]


def _is_bundled_busybox(resolved_exe: str | None) -> bool:
    """Return whether the executable uses a supported bundled BusyBox layout."""
    if not resolved_exe:
        return False
    name = Path(resolved_exe).name
    return name == "busybox" or name == "libbusybox.so"


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


def _get_available_shells() -> list[str]:
    """Return shell types whose executable can be resolved (cached)."""
    global _AVAILABLE_SHELLS
    if _AVAILABLE_SHELLS is None:
        _AVAILABLE_SHELLS = [
            name for name in _SHELL_SPECS if _resolve_shell_executable(name)
        ]
    return _AVAILABLE_SHELLS


def _resolve_timeout_arg(
    args: dict[str, Any], default_timeout: float
) -> tuple[float, str | None]:
    """Resolve per-call timeout, falling back to the configured default."""
    raw_timeout = args.get("timeout", default_timeout)
    if raw_timeout in (None, ""):
        raw_timeout = default_timeout
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        return 0.0, f"timeout must be numeric, got {raw_timeout!r}"
    if timeout < 0:
        return 0.0, "timeout must be >= 0"
    return timeout, None


def _wait_timeout(timeout: float) -> float | None:
    return timeout if timeout > 0 else None


def _format_timeout(timeout: float) -> str:
    return f"{timeout:g}s"


def _fake_wait_error(command: str) -> str | None:
    stripped = command.strip().lower()
    if stripped.startswith("echo") and any(
        w in stripped for w in ("waiting", "wait for", "still running", "in progress")
    ):
        return (
            "Do not use bash to fake-wait for background tasks. "
            "Background results arrive automatically. Just stop your response."
        )
    if stripped.startswith("sleep"):
        return (
            "Do not sleep to wait for background tasks. Results arrive "
            "automatically when ready. Just stop your response."
        )
    return None


def _shell_metadata(
    output_path: Path | None,
    shell_type: str,
    resolved_exe: str,
    env: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "shell_type": shell_type,
        "shell_executable": resolved_exe,
        "shell_override": _shell_override_env(shell_type),
        "home": env.get("HOME"),
        "timeout": timeout,
    }
    if output_path is not None:
        metadata["raw_output_path"] = str(output_path)
    return metadata


def _subprocess_runner(context: Any) -> Any:
    services = getattr(context, "runtime_services", {}) if context is not None else {}
    if isinstance(services, dict):
        return services.get("subprocess_runner")
    return None


# Negative values cannot collide with legitimate Linux process exit codes.
_BUSYBOX_TIMEOUT_SENTINEL = -2


def _run_busybox_blocking(
    *,
    argv: list[str],
    executable: str,
    kwargs: dict[str, Any],
    timeout: float | None,
) -> int:
    """Run bundled BusyBox with distinct executable and ``argv[0]`` values."""
    process = subprocess.Popen(
        argv,
        executable=executable,
        **kwargs,
    )
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if sys.platform != "win32":
                os.killpg(os.getpgid(process.pid), 9)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return _BUSYBOX_TIMEOUT_SENTINEL
    return process.returncode or 0


@register_builtin("bash")
class ShellTool(BaseTool):
    """Execute a command with the selected shell and working directory."""

    needs_context = True
    # Commands are opaque, so concurrent invocations cannot be proven independent.
    is_concurrency_safe = False

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
                "timeout": {
                    "type": "number",
                    "description": "Maximum execution time in seconds (0 = no timeout).",
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
        timeout, timeout_error = _resolve_timeout_arg(args, self.config.timeout)
        if timeout_error is not None:
            return ToolResult(error=timeout_error)

        wait_error = _fake_wait_error(command)
        if wait_error:
            return ToolResult(error=wait_error)

        shell_type = args.get("type", "bash").lower().strip()
        if shell_type not in _SHELL_SPECS:
            available = _get_available_shells()
            return ToolResult(
                error=f"Unknown shell type: {shell_type}. "
                f"Available: {', '.join(available) or 'none found'}"
            )

        spec_exe, _prefix_args = _SHELL_SPECS[shell_type]
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

        full_command = _build_shell_argv(shell_type, resolved_exe, command)

        logger.debug("Executing command", shell=shell_type, command=command[:100])

        env = os.environ.copy()
        if self.config.env:
            env.update(self.config.env)
        # System tool directories precede the bundled fallback so Android uses
        # executable platform binaries whenever they are available.
        if is_mobile_profile():
            android_path_parts = []
            for system_path in ("/system/bin", "/system/xbin"):
                if Path(system_path).is_dir():
                    android_path_parts.append(system_path)
            bin_dir = sandbox_bin_dir()
            if bin_dir is not None:
                android_path_parts.append(str(bin_dir))
            if android_path_parts:
                existing = env.get("PATH", "")
                env["PATH"] = os.pathsep.join(android_path_parts) + (
                    os.pathsep + existing if existing else ""
                )

        # Agent working directories take precedence over tool and process defaults.
        if context and getattr(context, "working_dir", None):
            cwd = str(context.working_dir)
        else:
            cwd = self.config.working_dir or os.getcwd()

        process = None
        output_path = None
        output_handle = None
        try:
            runner = _subprocess_runner(context)
            if runner is not None and hasattr(runner, "run_subprocess_exec"):
                result = await runner.run_subprocess_exec(
                    full_command,
                    cwd=cwd,
                    env=env,
                    timeout=_wait_timeout(timeout),
                    max_output_bytes=self.config.max_output or None,
                )
                output = (result.get("stdout", b"") + result.get("stderr", b"")).decode(
                    "utf-8", errors="replace"
                )
                exit_code = int(result.get("returncode") or 0)
                if result.get("timed_out"):
                    return ToolResult(
                        output=output,
                        error=f"Command timed out after {_format_timeout(timeout)}",
                        exit_code=-1,
                        metadata=_shell_metadata(
                            None, shell_type, resolved_exe, env, timeout
                        ),
                    )
            else:
                output_path, output_handle = _create_output_file()

                popen_kwargs: dict[str, Any] = {
                    "stdout": output_handle,
                    "stderr": subprocess.STDOUT,
                    "cwd": cwd,
                    "env": env,
                }
                if sys.platform == "win32":
                    popen_kwargs.update(windows_process_kwargs())
                else:
                    popen_kwargs["start_new_session"] = True

                # asyncio cannot separate the executable path from ``argv[0]``;
                # BusyBox native-library packaging requires both, so use Popen on
                # a worker thread for this platform-specific path.
                if _is_bundled_busybox(resolved_exe):
                    exit_code = await asyncio.to_thread(
                        _run_busybox_blocking,
                        argv=full_command,
                        executable=resolved_exe,
                        kwargs=popen_kwargs,
                        timeout=_wait_timeout(timeout),
                    )
                    if output_handle is not None:
                        output_handle.close()
                        output_handle = None
                    if exit_code == _BUSYBOX_TIMEOUT_SENTINEL:
                        output = _read_output_file(output_path)
                        return ToolResult(
                            output=output,
                            error=(
                                f"Command timed out after "
                                f"{_format_timeout(timeout)}"
                            ),
                            exit_code=-1,
                            metadata=_shell_metadata(
                                output_path, shell_type, resolved_exe, env, timeout
                            ),
                        )
                    output = _read_output_file(output_path)
                else:
                    process = await asyncio.create_subprocess_exec(
                        *full_command,
                        **popen_kwargs,
                    )

                    if output_handle is not None:
                        output_handle.close()
                        output_handle = None

                    try:
                        await asyncio.wait_for(
                            process.wait(), timeout=_wait_timeout(timeout)
                        )
                    except asyncio.TimeoutError:
                        await terminate_process_tree(process)
                        output = _read_output_file(output_path)
                        return ToolResult(
                            output=output,
                            error=(
                                f"Command timed out after "
                                f"{_format_timeout(timeout)}"
                            ),
                            exit_code=-1,
                            metadata=_shell_metadata(
                                output_path, shell_type, resolved_exe, env, timeout
                            ),
                        )
                    except asyncio.CancelledError:
                        await terminate_process_tree(process)
                        raise

                    output = _read_output_file(output_path)
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
                metadata=_shell_metadata(
                    output_path, shell_type, resolved_exe, env, timeout
                ),
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
                await terminate_process_tree(process)
            error_output = (
                _read_output_file(output_path)
                if output_path and output_path.exists()
                else ""
            )
            return ToolResult(
                output=error_output,
                error=str(e),
                metadata=_shell_metadata(
                    output_path, shell_type, resolved_exe, env, timeout
                ),
            )
        finally:
            if output_handle is not None:
                output_handle.close()


# Preserve the public class name used by existing configurations.
BashTool = ShellTool
