"""Session-local artifact helpers.

Binary artifacts (generated images, attachments, future file-style
outputs) live under a directory that is a sibling of the session's
``.kohakutr`` file: ``<session-stem>.artifacts/``.

This module owns:

* the path resolution for that directory,
* a safe relative-path validator used by write helpers, and
* the low-level bytes-to-disk writer with a symlink-safe escape guard.

Kept out of :mod:`kohakuterrarium.session.store` so the store module
stays under the 600-line cap.
"""

from pathlib import Path


def resolve_artifact_relpath(filename: str) -> Path:
    """Reject traversal and absolute paths; return a clean relative path.

    Cheap first line of defense. Callers still resolve the final
    location against the artifacts directory and re-check to catch
    symlink-based escapes (see :func:`write_artifact_bytes`).
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

    Path traversal is rejected via :func:`resolve_artifact_relpath`.
    After the full path is constructed, we resolve it and compare
    against the resolved artifacts root to catch symlink escapes.
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
