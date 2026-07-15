"""Session artifact filesystem serving (generated images, etc.).

Resolves session artifact directories and files while keeping transport
response handling outside the persistence layer.
"""

from pathlib import Path

from kohakuterrarium.errors import InvalidRequestError, NotFoundError
from kohakuterrarium.studio.persistence.store import resolve_session_path_default


def resolve_artifacts_dir(session_name: str, session_dir: Path) -> Path:
    """Return a session's sibling ``.artifacts`` directory.

    The directory may exist before the session file is finalized, so direct
    artifact-directory lookup precedes session-path resolution.
    """
    direct = session_dir / f"{session_name}.artifacts"
    if direct.is_dir():
        return direct
    session_path = resolve_session_path_default(session_name)
    if session_path is not None:
        sibling = session_path.parent / f"{session_path.stem}.artifacts"
        if sibling.is_dir():
            return sibling
    raise NotFoundError("session artifacts not found")


def resolve_artifact_file(artifacts: Path, filepath: str) -> Path:
    """Resolve an artifact file without allowing directory traversal.

    Empty, absolute, parent-traversing, or escaping paths are invalid. A valid
    path must resolve to an existing file.
    """
    if not filepath:
        raise InvalidRequestError("empty filepath")
    rel = Path(filepath)
    if rel.is_absolute() or any(part in ("..", "") for part in rel.parts):
        raise InvalidRequestError("invalid filepath")

    candidate = (artifacts / rel).resolve()
    try:
        candidate.relative_to(artifacts.resolve())
    except ValueError:
        raise InvalidRequestError("path escapes artifacts")
    if not candidate.is_file():
        raise NotFoundError("artifact not found")
    return candidate
