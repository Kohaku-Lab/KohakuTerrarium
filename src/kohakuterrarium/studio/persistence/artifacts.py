"""Session artifact filesystem serving (generated images, etc.).

Resolves session artifact directories and files while keeping transport
response handling outside the persistence layer.
"""

from pathlib import Path

from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.session.store.records.artifacts import (
    resolve_artifact_file as resolve_artifact_file,
)
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
