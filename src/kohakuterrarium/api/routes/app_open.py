"""Files the desktop shell was asked to open (a double-clicked session file)."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from kohakuterrarium.api.auth.dependencies import verify_admin_token
from kohakuterrarium.api.routes._local import local_only
from kohakuterrarium.studio.persistence.import_file import (
    SessionImportError,
    import_session_file,
)
from kohakuterrarium.studio.persistence.store import _session_dir

router = APIRouter()


class OpenSessionRequest(BaseModel):
    path: str


@router.post("/open-session", dependencies=[Depends(verify_admin_token)])
async def open_session(request: Request, body: OpenSessionRequest) -> dict[str, str]:
    """Make a ``.kohakutr`` file from this machine available as a saved session."""
    local_only(request, "session files can only be opened from this machine")
    try:
        name = import_session_file(Path(body.path), _session_dir())
    except SessionImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"session_name": name}
