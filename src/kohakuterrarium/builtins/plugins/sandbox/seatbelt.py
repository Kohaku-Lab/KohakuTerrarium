"""Kernel write enforcement for sandboxed subprocesses on macOS.

Builds a Seatbelt (``sandbox-exec``) profile from a :class:`SandboxProfile`'s
write axis so a spawned command and every process it starts can only write
where the plugin policy allows, whatever the command itself does.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

from kohakuterrarium.modules.sandbox.profile import SandboxProfile

SANDBOX_EXEC = "/usr/bin/sandbox-exec"

DEFAULT_WRITE_ALLOW: tuple[str, ...] = (
    "~/.cache",
    "~/Library/Caches",
    "~/.npm",
)

_ALWAYS_WRITABLE: tuple[str, ...] = ("/dev",)


def seatbelt_available() -> bool:
    """Whether this host can confine subprocesses with ``sandbox-exec``."""
    return sys.platform == "darwin" and shutil.which(SANDBOX_EXEC) is not None


def temp_roots() -> list[str]:
    """Shared and per-user temporary directories a command may write."""
    roots = ["/tmp", tempfile.gettempdir()]
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(tmpdir)
    return roots


def _real_paths(path: str, cwd: Path) -> list[str]:
    """The literal and symlink-resolved absolute forms of ``path``."""
    expanded = Path(os.path.expandvars(path)).expanduser()
    if not expanded.is_absolute():
        expanded = cwd / expanded
    forms = {os.path.normpath(str(expanded)), os.path.realpath(expanded)}
    return sorted(forms)


def _quote(path: str) -> str:
    escaped = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _subpaths(paths: list[str], cwd: Path) -> list[str]:
    seen: list[str] = []
    for path in paths:
        for form in _real_paths(path, cwd):
            if form not in seen:
                seen.append(form)
    return [f"(subpath {_quote(p)})" for p in seen]


def build_write_profile(
    profile: SandboxProfile,
    workspace: Path,
    *,
    write_allow: tuple[str, ...] | list[str] = DEFAULT_WRITE_ALLOW,
) -> str:
    """Seatbelt profile text enforcing ``profile``'s write axis.

    ``fs_write=workspace`` allows the working directory and temporary roots;
    ``fs_deny`` paths are carved out of those, and ``write_allow`` entries are
    carved back in, so a narrow exception such as ``~/.ssh/sockets`` survives a
    denied ``~/.ssh``. ``fs_write=deny`` allows only devices. Seatbelt applies
    the last matching rule. Reads, network and process rules are left open.
    """
    lines = ["(version 1)", "(allow default)"]
    if profile.fs_write != "broad":
        lines.append("(deny file-write*)")
        allowed = list(_ALWAYS_WRITABLE)
        if profile.fs_write == "workspace":
            allowed += [str(workspace), *temp_roots()]
        lines.append(f"(allow file-write* {' '.join(_subpaths(allowed, workspace))})")
    denied = [p for p in profile.fs_deny if not p.startswith("%")]
    if denied:
        lines.append(f"(deny file-write* {' '.join(_subpaths(denied, workspace))})")
    if write_allow and profile.fs_write != "deny":
        lines.append(
            f"(allow file-write* {' '.join(_subpaths(list(write_allow), workspace))})"
        )
    return "\n".join(lines) + "\n"


def confine_argv(argv: list[str], seatbelt_profile: str) -> list[str]:
    """``argv`` re-rooted under ``sandbox-exec`` with ``seatbelt_profile``."""
    return [SANDBOX_EXEC, "-p", seatbelt_profile, *argv]
