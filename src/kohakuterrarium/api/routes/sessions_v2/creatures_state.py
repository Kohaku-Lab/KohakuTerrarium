"""Expose per-creature scratchpad, trigger, environment, and runtime settings.

Service routing reaches each creature's home worker, while identifier resolution
accepts both canonical identifiers and session-scoped display names.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class ScratchpadPatch(BaseModel):
    updates: dict[str, str | None]


class WorkingDirRequest(BaseModel):
    path: str


class NativeToolOptionsRequest(BaseModel):
    tool: str
    values: dict[str, Any] = {}


@router.get("/{session_id}/creatures/{creature_id}/scratchpad")
async def get_scratchpad(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.get_scratchpad(cid)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.patch("/{session_id}/creatures/{creature_id}/scratchpad")
async def patch_scratchpad(
    session_id: str,
    creature_id: str,
    req: ScratchpadPatch,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.patch_scratchpad(cid, req.updates)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{session_id}/creatures/{creature_id}/triggers")
async def list_triggers(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.list_triggers(cid)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/env")
async def get_env(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.get_env(cid)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/system-prompt")
async def get_system_prompt(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.get_system_prompt(cid)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/working-dir")
async def get_working_dir(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        pwd = await service.get_working_dir(cid)
        return {"pwd": pwd}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.put("/{session_id}/creatures/{creature_id}/working-dir")
async def set_working_dir(
    session_id: str,
    creature_id: str,
    req: WorkingDirRequest,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        applied = await service.set_working_dir(cid, req.path)
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "saved", "pwd": applied}


@router.get("/{session_id}/creatures/{creature_id}/native-tool-options")
async def get_native_tool_options(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        tools = await service.native_tool_inventory(cid)
        return {"tools": tools}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.put("/{session_id}/creatures/{creature_id}/native-tool-options")
async def set_native_tool_options(
    session_id: str,
    creature_id: str,
    req: NativeToolOptionsRequest,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        applied = await service.set_native_tool_options(cid, req.tool, req.values or {})
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "saved", "tool": req.tool, "values": applied}
