"""Module CRUD routes — one router covers every kind."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from kohakuterrarium.api.routes.catalog._deps import get_workspace
from kohakuterrarium.studio.editors.codegen_init import RoundTripError
from kohakuterrarium.studio.editors.workspace_fs import KNOWN_KINDS
from kohakuterrarium.studio.editors.workspace_manifest import Workspace

router = APIRouter()


class ScaffoldBody(BaseModel):
    name: str
    template: str | None = None


class SaveBody(BaseModel):
    mode: str = "simple"
    form: dict = Field(default_factory=dict)
    execute_body: str = ""
    raw_source: str = ""


class DocSaveBody(BaseModel):
    """Write body for a tool / sub-agent skill-doc sidecar."""

    content: str = ""


def _check_kind(kind: str) -> None:
    if kind not in KNOWN_KINDS:
        raise HTTPException(
            400,
            detail={
                "code": "unknown_kind",
                "message": f"unknown module kind: {kind!r}",
                "valid_kinds": list(KNOWN_KINDS),
            },
        )


@router.get("/{kind}")
async def list_modules(kind: str, ws: Workspace = Depends(get_workspace)) -> list[dict]:
    _check_kind(kind)
    return ws.list_modules(kind)


@router.get("/{kind}/{name}")
async def load_module(
    kind: str, name: str, ws: Workspace = Depends(get_workspace)
) -> dict:
    _check_kind(kind)
    try:
        return ws.load_module(kind, name)
    except FileNotFoundError:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"{kind}/{name} not found",
            },
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})


@router.post("/{kind}", status_code=201)
async def scaffold_module(
    kind: str, body: ScaffoldBody, ws: Workspace = Depends(get_workspace)
) -> dict:
    _check_kind(kind)
    try:
        return ws.scaffold_module(kind, body.name, body.template)  # type: ignore[attr-defined]
    except FileExistsError:
        raise HTTPException(
            409,
            detail={
                "code": "name_exists",
                "message": f"{kind}/{body.name} already exists",
            },
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})


@router.put("/{kind}/{name}")
async def save_module(
    kind: str,
    name: str,
    body: SaveBody,
    ws: Workspace = Depends(get_workspace),
) -> dict:
    _check_kind(kind)
    try:
        return ws.save_module(kind, name, body.model_dump())
    except RoundTripError as e:
        raise HTTPException(
            422,
            detail={
                "code": "roundtrip_failed",
                "message": str(e),
            },
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_input", "message": str(e)})


@router.get("/{kind}/{name}/doc")
async def load_module_doc(
    kind: str,
    name: str,
    ws: Workspace = Depends(get_workspace),
) -> dict:
    _check_kind(kind)
    try:
        return ws.load_module_doc(kind, name)  # type: ignore[attr-defined]
    except FileNotFoundError:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"{kind}/{name} not found",
            },
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})


@router.put("/{kind}/{name}/doc")
async def save_module_doc(
    kind: str,
    name: str,
    body: DocSaveBody,
    ws: Workspace = Depends(get_workspace),
) -> dict:
    _check_kind(kind)
    try:
        return ws.save_module_doc(kind, name, body.content)  # type: ignore[attr-defined]
    except FileNotFoundError:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"{kind}/{name} not found — create the module first",
            },
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})


@router.delete("/{kind}/{name}")
async def delete_module(
    kind: str,
    name: str,
    confirm: bool = Query(False),
    ws: Workspace = Depends(get_workspace),
):
    _check_kind(kind)
    if not confirm:
        raise HTTPException(
            428,
            detail={
                "code": "confirm_required",
                "message": "pass ?confirm=true to delete",
            },
        )
    try:
        ws.delete_module(kind, name)
    except FileNotFoundError:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"{kind}/{name} not found",
            },
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})
    return {"ok": True}
