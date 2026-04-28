"""Persistence artifacts — serve files from a session's artifacts dir.

Path is ``/{session_name}/artifacts/{filepath:path}`` so the router can
be mounted under ``/api/sessions`` for URL preservation.
"""

import mimetypes
from urllib.parse import unquote

from fastapi import APIRouter
from fastapi.responses import FileResponse

from kohakuterrarium.studio.persistence import store as persistence_store
from kohakuterrarium.studio.persistence.artifacts import (
    resolve_artifact_file,
    resolve_artifacts_dir,
)

router = APIRouter()


@router.get("/{session_name}/artifacts/{filepath:path}")
async def get_session_artifact(session_name: str, filepath: str):
    """Serve a file from ``<session>.artifacts/`` with path-traversal guards.

    ``filepath`` is the path relative to the session's artifacts
    directory (e.g. ``generated_images/cat.png``). The resolved
    location must stay inside the artifacts dir — any ``..`` or
    absolute path is rejected.
    """
    decoded = unquote(filepath)
    artifacts = resolve_artifacts_dir(session_name, persistence_store._SESSION_DIR)
    candidate = resolve_artifact_file(artifacts, decoded)
    mime, _ = mimetypes.guess_type(candidate.name)
    return FileResponse(candidate, media_type=mime or "application/octet-stream")
