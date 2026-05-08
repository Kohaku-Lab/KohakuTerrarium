"""Creature CRUD + prompt file routes.

Workspace methods walk the filesystem (yaml read, prompt-file IO,
config validation) — every read goes through ``asyncio.to_thread``
so the studio editor doesn't block the runtime API while the user
edits configs side-by-side.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from kohakuterrarium.api.routes.catalog._deps import get_workspace
from kohakuterrarium.studio.editors.utils_paths import UnsafePath
from kohakuterrarium.studio.editors.workspace_manifest import Workspace

router = APIRouter()


class ScaffoldBody(BaseModel):
    name: str
    base_config: str | None = None
    description: str = ""


class SaveBody(BaseModel):
    config: dict = Field(default_factory=dict)
    prompts: dict[str, str] = Field(default_factory=dict)


class PromptBody(BaseModel):
    content: str


@router.get("")
async def list_creatures(ws: Workspace = Depends(get_workspace)) -> list[dict]:
    return await asyncio.to_thread(ws.list_creatures)


@router.get("/{name}")
async def load_creature(name: str, ws: Workspace = Depends(get_workspace)) -> dict:
    try:
        return await asyncio.to_thread(ws.load_creature, name)
    except ValueError as e:
        raise HTTPException(400, detail={"code": "unsafe_path", "message": str(e)})
    except FileNotFoundError:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"creature {name!r} not found",
            },
        )


@router.post("", status_code=201)
async def scaffold_creature(
    body: ScaffoldBody, ws: Workspace = Depends(get_workspace)
) -> dict:
    try:
        return await asyncio.to_thread(
            ws.scaffold_creature, body.name, body.base_config
        )  # type: ignore[attr-defined]
    except FileExistsError:
        raise HTTPException(
            409,
            detail={
                "code": "name_exists",
                "message": f"creature {body.name!r} already exists",
            },
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})


@router.put("/{name}")
async def save_creature(
    name: str,
    body: SaveBody,
    ws: Workspace = Depends(get_workspace),
) -> dict:
    try:
        return await asyncio.to_thread(
            ws.save_creature,
            name,
            {
                "config": body.config,
                "prompts": body.prompts,
            },
        )
    except (UnsafePath, ValueError) as e:
        raise HTTPException(400, detail={"code": "unsafe_path", "message": str(e)})


@router.delete("/{name}")
async def delete_creature(
    name: str,
    confirm: bool = Query(False),
    ws: Workspace = Depends(get_workspace),
):
    if not confirm:
        raise HTTPException(
            428,
            detail={
                "code": "confirm_required",
                "message": "pass ?confirm=true to delete",
            },
        )
    try:
        await asyncio.to_thread(ws.delete_creature, name)
    except FileNotFoundError:
        raise HTTPException(
            404,
            detail={
                "code": "not_found",
                "message": f"creature {name!r} not found",
            },
        )
    except ValueError as e:
        raise HTTPException(400, detail={"code": "invalid_name", "message": str(e)})
    return {"ok": True}


@router.get("/{name}/prompts/{rel:path}")
async def read_prompt(
    name: str, rel: str, ws: Workspace = Depends(get_workspace)
) -> dict:
    try:
        content = await asyncio.to_thread(ws.read_prompt, name, rel)
    except FileNotFoundError:
        raise HTTPException(404, detail={"code": "not_found", "message": rel})
    except (UnsafePath, ValueError) as e:
        raise HTTPException(400, detail={"code": "unsafe_path", "message": str(e)})
    return {"path": rel, "content": content}


@router.put("/{name}/prompts/{rel:path}")
async def write_prompt(
    name: str,
    rel: str,
    body: PromptBody,
    ws: Workspace = Depends(get_workspace),
) -> dict:
    try:
        await asyncio.to_thread(ws.write_prompt, name, rel, body.content)
    except (UnsafePath, ValueError) as e:
        raise HTTPException(400, detail={"code": "unsafe_path", "message": str(e)})
    return {"ok": True, "path": rel}
