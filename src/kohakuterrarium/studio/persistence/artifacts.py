"""Session artifact filesystem serving (generated images, etc.).

Verbatim port of ``api/routes/sessions.py``'s artifact helpers. The
HTTP route layer wraps the resolved path in a ``FileResponse``.
"""

from pathlib import Path

from kohakuterrarium.errors import InvalidRequestError, NotFoundError
from kohakuterrarium.studio.persistence.store import resolve_session_path_default


def resolve_artifacts_dir(session_name: str, session_dir: Path) -> Path:
    """Return the artifacts dir for a session; :class:`NotFoundError` if absent.

    Mirrors ``SessionStore.artifacts_dir``: sibling directory named
    ``<session-stem>.artifacts`` alongside the ``.kohakutr`` file.
    Either an existing session file OR an existing ``.artifacts/``
    directory is enough — there are transient runs where the store
    writes artifacts before the .kohakutr is closed.
    """
    # Fast path: ``<name>.artifacts/`` directly under the sessions dir.
    direct = session_dir / f"{session_name}.artifacts"
    if direct.is_dir():
        return direct
    # Fallback: resolve via the session file stem (handles ``.kt``).
    session_path = resolve_session_path_default(session_name)
    if session_path is not None:
        sibling = session_path.parent / f"{session_path.stem}.artifacts"
        if sibling.is_dir():
            return sibling
    raise NotFoundError("session artifacts not found")


def resolve_artifact_file(artifacts: Path, filepath: str) -> Path:
    """Resolve ``filepath`` inside ``artifacts`` with traversal guards.

    Returns the resolved file path. Raises :class:`InvalidRequestError`
    for any invalid input (empty / absolute / parent-traversal /
    outside ``artifacts``) and :class:`NotFoundError` when the resolved
    path is not a file.
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
