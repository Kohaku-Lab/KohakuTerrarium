"""Open, summarize, and close the active catalog workspace."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.routes.catalog._deps import get_workspace, set_workspace
from kohakuterrarium.errors import PackageError
from kohakuterrarium.packages.locations import (
    ensure_local_project,
    is_local_project,
    project_dir,
)
from kohakuterrarium.packages.resolve import is_package_ref, resolve_package_path
from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace
from kohakuterrarium.studio.editors.workspace_manifest import Workspace

router = APIRouter()


class OpenBody(BaseModel):
    path: str


def _resolve_root(path: str) -> str:
    """A filesystem path, or an ``@pkg/...`` ref; ``@`` creates the local project."""
    if not is_package_ref(path):
        return path
    if is_local_project(path[1:].split("/", 1)[0]):
        ensure_local_project()
    try:
        return str(resolve_package_path(path))
    except PackageError as e:
        raise HTTPException(400, detail={"code": "bad_ref", "message": str(e)})


@router.get("")
async def get_summary(ws: Workspace = Depends(get_workspace)) -> dict:
    return ws.summary()  # type: ignore[attr-defined]


@router.get("/project")
async def get_project() -> dict:
    root = project_dir()
    return {"root": str(root), "ref": "@", "exists": root.is_dir()}


@router.post("/open")
async def open_workspace(body: OpenBody) -> dict:
    try:
        ws = LocalWorkspace.open(_resolve_root(body.path))
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
