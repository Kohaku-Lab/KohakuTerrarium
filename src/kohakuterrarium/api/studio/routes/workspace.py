"""Workspace lifecycle routes.

* GET    /api/studio/workspace           — active summary (409 if none)
* POST   /api/studio/workspace/open      — switch to a new workspace
* POST   /api/studio/workspace/close     — clear active workspace
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.studio.deps import get_workspace, set_workspace
from kohakuterrarium.api.studio.workspace.base import Workspace
from kohakuterrarium.api.studio.workspace.local import LocalWorkspace

router = APIRouter()


class OpenBody(BaseModel):
    path: str


@router.get("")
async def get_summary(ws: Workspace = Depends(get_workspace)) -> dict:
    return ws.summary()  # type: ignore[attr-defined]


@router.post("/open")
async def open_workspace(body: OpenBody) -> dict:
    try:
        ws = LocalWorkspace.open(body.path)
    except FileNotFoundError as e:
        raise HTTPException(
            400,
            detail={
                "code": "not_found",
                "message": str(e),
            },
        )
    except NotADirectoryError as e:
        raise HTTPException(
            400,
            detail={
                "code": "not_a_directory",
                "message": str(e),
            },
        )
    set_workspace(ws)
    return ws.summary()


@router.post("/close", status_code=204)
async def close_workspace() -> None:
    set_workspace(None)
    return None
