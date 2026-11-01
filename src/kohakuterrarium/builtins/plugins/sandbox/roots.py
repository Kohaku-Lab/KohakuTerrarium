"""Where a sandboxed command may write, shared by every kernel backend.

The plugin's file-tool checks and each OS confinement backend read the same
roots, so a ``write`` tool call and a shell command agree on what is allowed.
"""

import os
import tempfile
from pathlib import Path

from kohakuterrarium.modules.sandbox.profile import SandboxProfile

CACHE_ROOTS: tuple[str, ...] = (
    "~/.cache",
    "~/Library/Caches",
    "~/.npm",
)

ALWAYS_WRITABLE: tuple[str, ...] = ("/dev",)


def temp_roots() -> list[str]:
    """Shared and per-user temporary directories a command may write."""
    roots = ["/tmp", tempfile.gettempdir()]
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(tmpdir)
    return roots


def workspace_roots(workspace: Path | str) -> list[str]:
    """Roots writable under ``fs_write=workspace``: cwd, temp roots, caches."""
    return [str(workspace), *temp_roots(), *CACHE_ROOTS]


def write_roots(profile: SandboxProfile, workspace: Path) -> list[str]:
    """Roots a command may write before ``fs_deny`` is carved out of them."""
    match profile.fs_write:
        case "broad":
            return ["/"]
        case "workspace":
            return [*ALWAYS_WRITABLE, *workspace_roots(workspace)]
        case _:
            return list(ALWAYS_WRITABLE)


def denied_paths(profile: SandboxProfile) -> list[str]:
    """``fs_deny`` entries that name real paths (``%``-prefixed ones are tags)."""
    return [p for p in profile.fs_deny if not p.startswith("%")]


def expand(path: str, cwd: Path) -> Path:
    """``path`` with variables and ``~`` expanded, made absolute against ``cwd``."""
    expanded = Path(os.path.expandvars(path)).expanduser()
    return expanded if expanded.is_absolute() else cwd / expanded
