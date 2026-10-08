"""Manage every supported workspace module kind through one router."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from kohakuterrarium.api.routes.catalog._deps import get_workspace
from kohakuterrarium.studio.editors.codegen_init import RoundTripError
from kohakuterrarium.studio.editors.module_check import check_module
from kohakuterrarium.studio.editors.starters import UnknownStarterError
from kohakuterrarium.studio.editors.workspace_fs import KNOWN_KINDS
from kohakuterrarium.studio.editors.workspace_manifest import Workspace

router = APIRouter()


class ScaffoldBody(BaseModel):
    name: str
    template: str | None = None
    plug_into: list[str] = Field(default_factory=list)


class PlugBody(BaseModel):
    creatures: list[str]


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
        return ws.scaffold_module(  # type: ignore[attr-defined]
            kind, body.name, body.template, body.plug_into
        )
    except FileExistsError:
        raise HTTPException(
            409,
            detail={
                "code": "name_exists",
                "message": f"{kind}/{body.name} already exists",
            },
        )
    except FileNotFoundError as e:
        raise HTTPException(
            404, detail={"code": "creature_not_found", "message": str(e)}
        )
    except UnknownStarterError as e:
        raise HTTPException(400, detail={"code": "unknown_starter", "message": str(e)})
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})


@router.get("/{kind}/{name}/wiring")
async def module_wiring(
    kind: str, name: str, ws: Workspace = Depends(get_workspace)
) -> dict:
    """How a creature config loads this module, and which creatures do."""
    _check_kind(kind)
    try:
        info = ws.module_wiring(kind, name)  # type: ignore[attr-defined]
        return {**info, "users": ws.module_users(kind, name)}  # type: ignore[attr-defined]
    except FileNotFoundError:
        raise HTTPException(
            404, detail={"code": "not_found", "message": f"{kind}/{name} not found"}
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})


async def _edit_wiring(kind: str, name: str, body: PlugBody, ws, plug: bool) -> dict:
    _check_kind(kind)
    try:
        edit = ws.plug_module if plug else ws.unplug_module
        changed = edit(kind, name, body.creatures)
        return {"changed": changed, "users": ws.module_users(kind, name)}
    except FileNotFoundError as e:
        raise HTTPException(404, detail={"code": "not_found", "message": str(e)})
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})


@router.post("/{kind}/{name}/check")
async def check(kind: str, name: str, ws: Workspace = Depends(get_workspace)) -> dict:
    """Compile and load the saved module as a creature would; ``{ok, errors, loaded}``."""
    _check_kind(kind)
    try:
        return await asyncio.to_thread(check_module, ws, kind, name)
    except FileNotFoundError:
        raise HTTPException(
            404, detail={"code": "not_found", "message": f"{kind}/{name} not found"}
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})


@router.post("/{kind}/{name}/plug")
async def plug_module(
    kind: str, name: str, body: PlugBody, ws: Workspace = Depends(get_workspace)
) -> dict:
    """Wire the module into the named workspace creatures."""
    return await _edit_wiring(kind, name, body, ws, plug=True)


@router.post("/{kind}/{name}/unplug")
async def unplug_module(
    kind: str, name: str, body: PlugBody, ws: Workspace = Depends(get_workspace)
) -> dict:
    """Remove the module from the named workspace creatures."""
    return await _edit_wiring(kind, name, body, ws, plug=False)


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
