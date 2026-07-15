"""Persistence artifacts — serve files from a session's artifacts dir.

The ``/{session_name}/artifacts/{filepath:path}`` path preserves the public URL
when this router is mounted under ``/api/sessions``.
"""

import asyncio
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


def _resolve_artifact(session_name: str, decoded: str):
    """Resolve an artifact directory and candidate in one synchronous unit.

    Both operations inspect the filesystem, so grouping them requires only one
    event-loop-to-worker handoff.
    """
    artifacts = resolve_artifacts_dir(session_name, persistence_store._SESSION_DIR)
    return resolve_artifact_file(artifacts, decoded)


@router.get("/{session_name}/artifacts/{filepath:path}")
async def get_session_artifact(session_name: str, filepath: str):
    """Serve a session artifact while enforcing directory containment.

    ``filepath`` is relative to ``<session>.artifacts/``. Absolute paths and
    traversal outside that directory are rejected by resolution. Filesystem
    inspection runs in a worker thread, while ``FileResponse`` handles streaming
    without blocking the event loop.
    """
    decoded = unquote(filepath)
    candidate = await asyncio.to_thread(_resolve_artifact, session_name, decoded)
    mime, _ = mimetypes.guess_type(candidate.name)
    return FileResponse(candidate, media_type=mime or "application/octet-stream")
