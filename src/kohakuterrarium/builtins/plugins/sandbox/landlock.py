"""Kernel write enforcement for sandboxed subprocesses on Linux.

Turns a :class:`SandboxProfile`'s write axis into the list of paths a
Landlock ruleset grants, and wraps a command with the ``landlock_exec``
launcher that applies it. Landlock only grants, so an ``fs_deny`` path inside
a writable root is carved out by granting the root's other entries instead.
"""

import functools
import json
import os
import sys
from pathlib import Path

from kohakuterrarium.builtins.plugins.sandbox.landlock_exec import landlock_abi
from kohakuterrarium.builtins.plugins.sandbox.roots import (
    denied_paths,
    expand,
    write_roots,
)
from kohakuterrarium.modules.sandbox.profile import SandboxProfile

LAUNCHER = Path(__file__).with_name("landlock_exec.py")


@functools.cache
def landlock_available() -> bool:
    """Whether this host can confine subprocesses with Landlock."""
    return landlock_abi() >= 1


def _real(path: str, cwd: Path) -> Path:
    return Path(os.path.realpath(expand(path, cwd)))


def _within(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def _grant_beneath(path: Path, denied: list[Path], out: list[str]) -> None:
    """Grant ``path`` whole, or its entries one by one around denied paths."""
    if any(_within(path, d) for d in denied):
        return
    if not any(_within(d, path) for d in denied):
        out.append(str(path))
        return
    try:
        entries = list(os.scandir(path))
    except OSError:
        return
    for entry in entries:
        if not entry.is_symlink():
            _grant_beneath(Path(entry.path), denied, out)


def write_grants(
    profile: SandboxProfile,
    workspace: Path,
    *,
    write_allow: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """Existing paths a command may write under ``profile``'s write axis.

    Writable roots come from :func:`write_roots`; an ``fs_deny`` path
    inside one is left out along with the directories above it, so nothing
    can be created directly in those directories while their other entries
    stay writable. ``write_allow`` entries are granted even inside
    ``fs_deny``. Paths that do not exist yet cannot be granted.
    """
    denied = [_real(p, workspace) for p in denied_paths(profile)]
    grants: list[str] = []
    for root in write_roots(profile, workspace):
        real = _real(root, workspace)
        if real.exists():
            _grant_beneath(real, denied, grants)
    if profile.fs_write != "deny":
        grants += [str(_real(p, workspace)) for p in write_allow]
    return list(dict.fromkeys(grants))


def confine_argv(argv: list[str], grants: list[str]) -> list[str]:
    """``argv`` run through the Landlock launcher with ``grants`` writable."""
    return [sys.executable, "-I", "-S", str(LAUNCHER), json.dumps(grants), "--", *argv]
