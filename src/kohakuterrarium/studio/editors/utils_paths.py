"""Path-safety helpers for studio FS operations.

Validates user-supplied names and resolves relative paths without allowing
workspace escape. Workspace I/O boundaries rely on these checks.
"""

from pathlib import Path

# Reject Windows device names on every platform so workspaces remain portable.
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class UnsafePath(ValueError):
    """Raised when a user-supplied path escapes its root."""


def sanitize_name(name: str) -> str:
    """Validate and return a portable creature or module name.

    Empty, hidden, whitespace-padded, path-like, parent, and reserved names are
    rejected.
    """
    if not name or not isinstance(name, str):
        raise ValueError("name must be a non-empty string")
    if name != name.strip():
        raise ValueError("name must not have leading/trailing whitespace")
    if name.startswith("."):
        raise ValueError(f"name must not start with '.': {name!r}")
    if "/" in name or "\\" in name:
        raise ValueError(f"name must not contain path separators: {name!r}")
    if ".." in name.split("/"):
        raise ValueError(f"name must not contain '..': {name!r}")
    if name.lower() in _WINDOWS_RESERVED:
        raise ValueError(f"name is a reserved OS name: {name!r}")
    return name


def ensure_in_root(root: Path, rel: str) -> Path:
    """Resolve a relative path and require containment within ``root``.

    Absolute and escaping paths raise :class:`UnsafePath`; valid inputs return an
    absolute resolved path.
    """
    if not rel:
        raise UnsafePath("empty relative path")
    p = Path(rel)
    if p.is_absolute():
        raise UnsafePath(f"absolute path not allowed: {rel!r}")
    root_resolved = root.resolve()
    target = (root / p).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as e:
        raise UnsafePath(f"path {rel!r} escapes workspace root {root_resolved}") from e
    return target
