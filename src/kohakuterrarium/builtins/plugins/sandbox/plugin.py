"""Enforce runtime-configurable file, network, tool, and subprocess policy.

The sandbox remains a plugin: hooks gate known tools and any tool's declared
``path_arguments``, while a runtime service provides the same policy boundary
to subprocess-capable tools.
"""

import asyncio
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from kohakuterrarium.builtins.plugins.sandbox.seatbelt import (
    CACHE_ROOTS,
    build_write_profile,
    confine_argv,
    seatbelt_available,
    temp_roots,
)
from kohakuterrarium.builtins.tools.subprocess.shell_utils import (
    terminate_process_tree,
    windows_process_kwargs,
)
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
    PluginLockedError,
)
from kohakuterrarium.modules.sandbox.parse import parse_profile
from kohakuterrarium.modules.sandbox.presets import WORKSPACE
from kohakuterrarium.modules.sandbox.profile import DEFAULT_DENY_PATHS, SandboxProfile
from kohakuterrarium.modules.tool.base import resolve_tool_path
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

READ_PATH_ARGS = {
    "read": ["path"],
    "notebook_read": ["path"],
    "grep": ["path"],
    "tree": ["path"],
}

WRITE_PATH_ARGS = {
    "write": ["path"],
    "edit": ["path"],
    "multi_edit": ["path"],
    "notebook_edit": ["path"],
}

NETWORK_URL_ARGS = {
    "web_fetch": ["url"],
}

NETWORK_TOOLS = {"web_search"}

UNLOCK_ENV = "KT_SANDBOX_ALLOW_UNLOCK"


class SandboxPlugin(BasePlugin):
    """Enforce a hard sandbox policy through plugin hooks."""

    name = "sandbox"
    description = "Hard sandbox policy for file/network/subprocess execution"
    priority = 1
    inherit_fork_policy = True

    @classmethod
    def option_schema(cls) -> dict[str, dict[str, Any]]:
        return {
            "enabled": {
                "type": "bool",
                "default": True,
                "doc": "Enable sandbox plugin checks and subprocess service.",
            },
            "backend": {
                "type": "enum",
                "values": ["auto", "audit", "off"],
                "default": "auto",
                "doc": "auto = block violations, audit = log only, off = fully disabled.",
            },
            "profile": {
                "type": "enum",
                "values": ["PURE", "READ_ONLY", "WORKSPACE", "NETWORK", "SHELL"],
                "default": "WORKSPACE",
                "doc": "Agent-level sandbox profile. Default WORKSPACE allows network.",
            },
            "fs_read": {
                "type": "enum",
                "values": ["default", "deny", "workspace", "broad"],
                "default": "default",
                "doc": "Override file-read capability.",
            },
            "fs_write": {
                "type": "enum",
                "values": ["default", "deny", "workspace", "broad"],
                "default": "default",
                "doc": "Override file-write capability.",
            },
            "network": {
                "type": "enum",
                "values": ["default", "deny", "allow"],
                "default": "default",
                "doc": "Override network capability for known web/subprocess paths.",
            },
            "syscall": {
                "type": "enum",
                "values": ["default", "pure", "fs", "shell", "any"],
                "default": "default",
                "doc": "Override subprocess capability marker.",
            },
            "env": {
                "type": "enum",
                "values": ["default", "filtered", "inherit"],
                "default": "default",
                "doc": "Subprocess environment handling marker.",
            },
            "tmp": {
                "type": "enum",
                "values": ["default", "private", "shared"],
                "default": "default",
                "doc": "Subprocess temporary-directory handling marker.",
            },
            "fs_deny": {
                "type": "list",
                "item_type": "string",
                "default": [],
                "doc": "Additional paths denied even when broader FS is allowed.",
            },
            "network_allowlist": {
                "type": "list",
                "item_type": "string",
                "default": [],
                "doc": "Allowed hosts when network is enabled. Empty = unrestricted.",
            },
            "os_enforce": {
                "type": "enum",
                "values": ["auto", "require", "off"],
                "default": "auto",
                "doc": (
                    "Kernel write confinement for subprocesses (macOS sandbox-exec). "
                    "auto = confine where supported, require = refuse to spawn "
                    "elsewhere, off = policy checks only."
                ),
            },
            "fs_write_allow": {
                "type": "list",
                "item_type": "string",
                "default": [],
                "doc": "Paths writable even inside fs_deny (exceptions you name).",
            },
            "locked": {
                "type": "bool",
                "default": False,
                "doc": (
                    "Refuse runtime disable and option changes. Unlock by "
                    "restarting the server with KT_SANDBOX_ALLOW_UNLOCK=1."
                ),
            },
            "blocked_tools": {
                "type": "list",
                "item_type": "string",
                "default": [],
                "doc": "Tool names blocked before execution.",
            },
        }

    def __init__(self, options: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__()
        opts = {key: spec.get("default") for key, spec in self.option_schema().items()}
        opts.update(kwargs)
        opts.update(options or {})
        self.options = opts
        self._context: PluginContext | None = None
        self._profile = WORKSPACE
        self._enabled = True
        self._audit = False
        self._blocked_tools: set[str] = set()
        self._network_allowlist: set[str] = set()
        self.refresh_options()

    async def on_load(self, context: PluginContext) -> None:
        self._context = context

    async def on_unload(self) -> None:
        self._context = None

    def set_options(self, values: dict[str, Any]) -> dict[str, Any]:
        """Apply option overrides unless the plugin is locked against them."""
        changes = {k: v for k, v in (values or {}).items() if self.options.get(k) != v}
        if changes and self._locked():
            raise PluginLockedError(
                "sandbox is locked; restart the server with "
                f"{UNLOCK_ENV}=1 to change {sorted(changes)}"
            )
        return super().set_options(values)

    def refuses_disable(self) -> bool:
        """A locked sandbox cannot be switched off at runtime."""
        return self._locked()

    def pins_working_dir(self) -> bool:
        """A locked sandbox keeps its workspace root: the working directory."""
        return self._enabled and self._locked()

    def _locked(self) -> bool:
        return bool(self.options.get("locked")) and os.environ.get(UNLOCK_ENV) != "1"

    def refresh_options(self) -> None:
        """Rebuild policy from runtime-editable plugin options."""
        backend = str(self.options.get("backend") or "auto").lower()
        self._enabled = bool(self.options.get("enabled", True)) and backend != "off"
        self._audit = backend == "audit"
        self._profile = _build_profile(self.options)
        self._blocked_tools = set(self.options.get("blocked_tools") or [])
        self._network_allowlist = {
            str(host).lower() for host in self.options.get("network_allowlist") or []
        }
        self._os_enforce = str(self.options.get("os_enforce") or "auto").lower()
        self._write_allow = tuple(
            str(p) for p in self.options.get("fs_write_allow") or []
        )

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> dict | None:
        """Gate known file/network/tool operations before execution."""
        if not self._enabled:
            return None
        tool_name = str(kwargs.get("tool_name") or "")
        context = kwargs.get("context")
        if tool_name in self._blocked_tools:
            self._violate("tool", tool_name, f"tool '{tool_name}' is blocked")
        cwd = _context_cwd(context, self._context)
        if tool_name == "glob":
            self._check_glob(args, context, cwd)
        declared = _declared_path_arguments(
            kwargs.get("tool") or self._lookup_tool(tool_name)
        )
        for path in _iter_tool_paths(tool_name, args, cwd, declared):
            self._check_path(tool_name, path.path, path.operation, cwd)
        self._check_network_tool(tool_name, args)
        return None

    def _lookup_tool(self, tool_name: str) -> Any:
        """Find the host agent's tool by name when the hook got no instance."""
        agent = self._context.host_agent if self._context is not None else None
        registry = getattr(agent, "registry", None)
        if registry is None or not tool_name:
            return None
        return registry.get_tool(tool_name)

    def runtime_services(self, context: Any) -> dict[str, Any]:
        """Expose the optional subprocess runner service while enabled."""
        if not self._enabled:
            return {}
        return {"subprocess_runner": self}

    async def run_subprocess_exec(
        self,
        argv: list[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        input_data: bytes | None = None,
        max_output_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Validate subprocess policy before delegating execution to the host OS."""
        if not self._enabled:
            return await _run_plain_subprocess(
                argv,
                cwd=cwd,
                env=env,
                timeout=timeout,
                input_data=input_data,
                max_output_bytes=max_output_bytes,
            )
        if self._profile.syscall == "pure":
            self._violate("syscall", "spawn", "subprocess execution is denied")
        if self._profile.syscall == "fs" and self._profile.fs_write == "deny":
            self._violate("syscall", "spawn", "read-only subprocess is denied")
        if self._profile.network == "deny" and _argv_may_use_network(argv):
            self._violate("network", " ".join(argv), "network command denied")
        return await _run_plain_subprocess(
            self._confine(argv, cwd),
            cwd=cwd,
            env=env,
            timeout=timeout,
            input_data=input_data,
            max_output_bytes=max_output_bytes,
        )

    def _confine(self, argv: list[str], cwd: str | Path | None) -> list[str]:
        """Wrap ``argv`` so the kernel enforces the write policy, when it can."""
        if self._audit or self._os_enforce == "off":
            return argv
        if not seatbelt_available():
            if self._os_enforce == "require":
                self._violate(
                    "syscall", "spawn", "kernel sandbox is unavailable on this host"
                )
            return argv
        workspace = (
            Path(cwd).resolve()
            if cwd is not None
            else _context_cwd(None, self._context)
        )
        return confine_argv(
            argv,
            build_write_profile(
                self._profile, workspace, write_allow=self._write_allow
            ),
        )

    def _check_glob(self, args: dict[str, Any], context: Any, cwd: Path) -> None:
        """Check the tool's actual base and literal pattern prefix."""
        base = resolve_tool_path(args.get("path", "."), context)
        self._check_path("glob", base, "read", cwd, literal=True)
        pattern = str(args.get("pattern", "")).replace("\\", "/")
        if pattern.startswith("/") or PureWindowsPath(pattern).drive:
            self._violate("fs_read", pattern, "glob: pattern must be relative to path")
            return
        prefix: list[str] = []
        wildcard = False
        for part in pattern.split("/"):
            if part == ".." and wildcard:
                self._violate(
                    "fs_read",
                    pattern,
                    "glob: parent traversal after a wildcard is unsupported",
                )
                return
            wildcard = wildcard or any(char in part for char in "*?[")
            if not wildcard:
                prefix.append(part)
        if prefix:
            self._check_path("glob", base.joinpath(*prefix), "read", cwd, literal=True)

    def _check_path(
        self,
        tool_name: str,
        path: Path,
        operation: str,
        cwd: Path,
        *,
        literal: bool = False,
    ) -> None:
        axis = "fs_write" if operation == "write" else "fs_read"
        level = getattr(self._profile, axis)
        resolved = (
            path if literal else Path(os.path.expandvars(str(path))).expanduser()
        ).resolve()
        writing = axis == "fs_write"
        if (
            writing
            and level != "deny"
            and self._under(resolved, self._write_allow, cwd)
        ):
            return
        for denied in self._profile.fs_deny:
            denied_path = _resolve(denied, cwd).resolve()
            if _is_relative_to(resolved, denied_path):
                self._violate(axis, str(path), f"{tool_name}: path is denied")
        if level == "deny":
            self._violate(axis, str(path), f"{tool_name}: {axis}=deny")
        roots = [str(cwd), *temp_roots(), *CACHE_ROOTS] if writing else [str(cwd)]
        if level == "workspace" and not self._under(resolved, roots, cwd):
            self._violate(
                axis,
                str(path),
                f"{tool_name}: path is outside working directory ({cwd})",
            )

    @staticmethod
    def _under(resolved: Path, roots: tuple[str, ...] | list[str], cwd: Path) -> bool:
        """Whether ``resolved`` lies under any of ``roots``."""
        return any(_is_relative_to(resolved, _resolve(r, cwd).resolve()) for r in roots)

    def _check_network_tool(self, tool_name: str, args: dict[str, Any]) -> None:
        if tool_name in NETWORK_TOOLS:
            self._check_network(tool_name, "https://duckduckgo.com/")
        for arg_name in NETWORK_URL_ARGS.get(tool_name, []):
            raw = args.get(arg_name)
            if raw:
                url = str(raw)
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                self._check_network(tool_name, url)

    def _check_network(self, tool_name: str, url: str) -> None:
        if self._profile.network == "deny":
            self._violate("network", url, f"{tool_name}: network access denied")
        host = urlparse(url).hostname or ""
        if self._network_allowlist and host not in self._network_allowlist:
            self._violate("network", url, f"{tool_name}: host '{host}' not allowlisted")

    def _violate(self, axis: str, requested: str, message: str) -> None:
        full = f"SandboxViolation[{axis}]: {message} ({requested})"
        if self._audit:
            logger.warning(full)
            return
        raise PluginBlockError(full)


@dataclass(slots=True)
class _ToolPath:
    path: Path
    operation: str


def _build_profile(options: dict[str, Any]) -> SandboxProfile:
    profile = parse_profile(options.get("profile") or "WORKSPACE")
    updates: dict[str, Any] = {}
    for key in ("fs_read", "fs_write", "network", "syscall", "env", "tmp"):
        value = options.get(key)
        if value and value != "default":
            updates[key] = value
    deny = list(DEFAULT_DENY_PATHS)
    deny.extend(str(p) for p in options.get("fs_deny") or [])
    updates["fs_deny"] = deny
    return profile.with_overrides(**updates)


def _context_cwd(context: Any, plugin_context: PluginContext | None) -> Path:
    if context is not None and getattr(context, "working_dir", None) is not None:
        return Path(context.working_dir).resolve()
    if plugin_context is not None:
        return Path(plugin_context.working_dir).resolve()
    return Path.cwd().resolve()


def _declared_path_arguments(tool: Any) -> dict[str, str]:
    declared = getattr(tool, "path_arguments", None)
    if not isinstance(declared, dict):
        return {}
    return {
        str(name): "write" if operation == "write" else "read"
        for name, operation in declared.items()
    }


def _iter_tool_paths(
    tool_name: str,
    args: dict[str, Any],
    cwd: Path,
    declared: dict[str, str] | None = None,
) -> list[_ToolPath]:
    checks = {(arg, "read") for arg in READ_PATH_ARGS.get(tool_name, [])}
    checks |= {(arg, "write") for arg in WRITE_PATH_ARGS.get(tool_name, [])}
    checks |= {(arg, op) for arg, op in (declared or {}).items()}
    paths: list[_ToolPath] = []
    for arg_name, operation in sorted(checks):
        paths.extend(_paths_from_value(args.get(arg_name), operation, cwd))
    return paths


def _paths_from_value(value: Any, operation: str, cwd: Path) -> list[_ToolPath]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out: list[_ToolPath] = []
        for item in value:
            out.extend(_paths_from_value(item, operation, cwd))
        return out
    text = str(value)
    if operation == "read" and any(ch in text for ch in "*?["):
        base = _glob_base(text)
        return [_ToolPath(_resolve(base, cwd), operation)]
    return [_ToolPath(_resolve(text, cwd), operation)]


def _glob_base(pattern: str) -> str:
    parts = Path(pattern).parts
    safe: list[str] = []
    for part in parts:
        if any(ch in part for ch in "*?["):
            break
        safe.append(part)
    if not safe:
        return "."
    return str(Path(*safe))


def _resolve(path: str, cwd: Path) -> Path:
    p = Path(os.path.expandvars(path)).expanduser()
    if not p.is_absolute():
        p = cwd / p
    return p


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _argv_may_use_network(argv: list[str]) -> bool:
    network_bins = {
        "curl",
        "wget",
        "git",
        "pip",
        "pip3",
        "npm",
        "pnpm",
        "yarn",
        "uv",
        "python",
        "python3",
    }
    first = Path(argv[0]).name if argv else ""
    joined = " ".join(shlex.quote(a) for a in argv)
    return first in network_bins or "://" in joined


async def _run_plain_subprocess(
    argv: list[str],
    *,
    cwd: str | Path | None,
    env: dict[str, str] | None,
    timeout: float | None,
    input_data: bytes | None,
    max_output_bytes: int | None,
) -> dict[str, Any]:
    """Run a subprocess and return bounded byte streams and timeout state."""
    spawn_kwargs: dict[str, Any] = (
        windows_process_kwargs()
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        stdin=asyncio.subprocess.PIPE if input_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **spawn_kwargs,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input_data), timeout=timeout if timeout else None
        )
    except asyncio.CancelledError:
        await terminate_process_tree(process)
        raise
    except asyncio.TimeoutError:
        await terminate_process_tree(process)
        return {
            "returncode": 124,
            "stdout": b"",
            "stderr": f"Command timed out after {timeout} seconds".encode(),
            "timed_out": True,
        }
    if max_output_bytes and len(stdout) + len(stderr) > max_output_bytes:
        stdout = stdout[:max_output_bytes]
        stderr = stderr[: max(0, max_output_bytes - len(stdout))]
    return {
        "returncode": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": False,
    }
