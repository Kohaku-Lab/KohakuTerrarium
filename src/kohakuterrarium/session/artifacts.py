"""Resolve and safely write session-local binary artifacts.

Artifacts live beside the session file under ``<session-stem>.artifacts/``.
"""

from pathlib import Path


def resolve_artifact_relpath(filename: str) -> Path:
    """Reject traversal and absolute paths; return a clean relative path.

    The final resolved path still requires containment validation to reject
    symlink-based escapes.
    """
    if not filename:
        raise ValueError("artifact filename must be non-empty")
    p = Path(filename)
    if p.is_absolute():
        raise ValueError(f"artifact filename must be relative: {filename!r}")
    parts = p.parts
    if any(part in ("..", "") for part in parts):
        raise ValueError(f"artifact filename contains traversal: {filename!r}")
    return p


def artifacts_dir_for(session_path: Path) -> Path:
    """Return (and create) the ``<stem>.artifacts/`` dir for a session file."""
    target = session_path.parent / f"{session_path.stem}.artifacts"
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_artifact_bytes(artifacts_dir: Path, filename: str, data: bytes) -> Path:
    """Write ``data`` to ``artifacts_dir/<filename>`` safely.

    Both lexical traversal and resolved symlink escapes are rejected before data
    reaches disk.
    """
    safe_rel = resolve_artifact_relpath(filename)
    path = artifacts_dir / safe_rel
    resolved = path.resolve()
    art_root = artifacts_dir.resolve()
    try:
        resolved.relative_to(art_root)
    except ValueError:
        raise ValueError(f"artifact path escapes artifacts_dir: {filename!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
