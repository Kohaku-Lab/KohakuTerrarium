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

CACHE_ROOTS: tuple[str, ...] = (
    "~/.cache",
    "~/Library/Caches",
    "~/.npm",
)

_ALWAYS_WRITABLE: tuple[str, ...] = ("/dev",)

# Programs and services that start work outside the caller's sandbox
# (LaunchServices, Apple Events, launchd jobs).
_LAUNCHERS: tuple[str, ...] = ("/usr/bin/open", "/usr/bin/osascript", "/bin/launchctl")
# Control sockets of daemons and multiplexers that run commands unconfined.
_CONTROL_SOCKETS: tuple[str, ...] = (
    "/var/run/docker.sock",
    "~/.docker/run",
    "~/.screen",
    "~/Library/Application Support/iTerm2",
)

_LAUNCH_SERVICES: tuple[str, ...] = (
    '(global-name "com.apple.coreservices.launchservicesd")',
    '(global-name-prefix "com.apple.lsd.")',
    '(global-name "com.apple.coreservices.appleevents")',
)


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


def _ancestors(paths: list[str], cwd: Path) -> list[str]:
    """Every parent directory of ``paths`` below the filesystem root."""
    seen: list[str] = []
    for path in paths:
        for form in _real_paths(path, cwd):
            for parent in Path(form).parents:
                text = str(parent)
                if text != parent.anchor and text not in seen:
                    seen.append(text)
    return [f"(literal {_quote(p)})" for p in seen]


def build_write_profile(
    profile: SandboxProfile,
    workspace: Path,
    *,
    write_allow: tuple[str, ...] | list[str] = (),
) -> str:
    """Seatbelt profile text enforcing ``profile``'s write axis.

    ``fs_write=workspace`` allows the working directory, temporary roots and
    user caches; ``fs_deny`` paths, and the directory entries above them, are
    carved out of that, and ``write_allow`` entries are carved back in, so an
    exception such as ``~/.ssh/sockets`` survives a denied ``~/.ssh``.
    ``fs_write=deny`` allows only devices. Seatbelt applies the last matching
    rule. Launchers that would run work outside the sandbox are refused; reads
    and network are left open.
    """
    lines = ["(version 1)", "(allow default)"]
    if profile.fs_write != "broad":
        lines.append("(deny file-write*)")
        allowed = list(_ALWAYS_WRITABLE)
        if profile.fs_write == "workspace":
            allowed += [str(workspace), *temp_roots(), *CACHE_ROOTS]
        lines.append(f"(allow file-write* {' '.join(_subpaths(allowed, workspace))})")
    denied = [p for p in profile.fs_deny if not p.startswith("%")]
    if denied:
        rules = _subpaths(denied, workspace) + _ancestors(denied, workspace)
        lines.append(f"(deny file-write* {' '.join(rules)})")
    if write_allow and profile.fs_write != "deny":
        lines.append(
            f"(allow file-write* {' '.join(_subpaths(list(write_allow), workspace))})"
        )
    launchers = " ".join(f"(literal {_quote(p)})" for p in _LAUNCHERS)
    lines.append(f"(deny process-exec {launchers})")
    lines.append(f"(deny mach-lookup {' '.join(_LAUNCH_SERVICES)})")
    sockets = [*_CONTROL_SOCKETS, f"/private/tmp/tmux-{os.getuid()}"]
    lines.append(f"(deny network-outbound {' '.join(_subpaths(sockets, workspace))})")
    return "\n".join(lines) + "\n"


def confine_argv(argv: list[str], seatbelt_profile: str) -> list[str]:
    """``argv`` re-rooted under ``sandbox-exec`` with ``seatbelt_profile``."""
    return [SANDBOX_EXEC, "-p", seatbelt_profile, *argv]
