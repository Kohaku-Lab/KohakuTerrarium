"""Session artifact filesystem serving (generated images, etc.).

Verbatim port of ``api/routes/sessions.py``'s artifact helpers. The
HTTP route layer wraps the resolved path in a ``FileResponse``.
"""

from pathlib import Path

from fastapi import HTTPException

from kohakuterrarium.studio.persistence.store import resolve_session_path_default


def resolve_artifacts_dir(session_name: str, session_dir: Path) -> Path:
    """Return the artifacts dir for a session, or 404 if it doesn't exist.

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
    raise HTTPException(status_code=404, detail="session artifacts not found")


def resolve_artifact_file(artifacts: Path, filepath: str) -> Path:
    """Resolve ``filepath`` inside ``artifacts`` with traversal guards.

    Returns the resolved file path. Raises ``HTTPException`` for any
    invalid input (empty / absolute / parent-traversal / outside
    ``artifacts`` / not a file).
    """
    if not filepath:
        raise HTTPException(status_code=400, detail="empty filepath")
    rel = Path(filepath)
    if rel.is_absolute() or any(part in ("..", "") for part in rel.parts):
        raise HTTPException(status_code=400, detail="invalid filepath")

    candidate = (artifacts / rel).resolve()
    try:
        candidate.relative_to(artifacts.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes artifacts")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return candidate
