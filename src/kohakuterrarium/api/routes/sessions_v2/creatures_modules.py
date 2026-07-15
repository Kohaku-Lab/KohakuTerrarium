"""Manage configurable modules for a session-scoped creature.

Service routing reaches remote home workers, and identifier resolution accepts
both canonical creature identifiers and display names.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class ModuleOptionsRequest(BaseModel):
    values: dict[str, Any] = {}


@router.get("/{session_id}/creatures/{creature_id}/modules")
async def list_modules(
    session_id: str,
    creature_id: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        modules = await service.list_modules(cid)
        return {"modules": modules}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get(
    "/{session_id}/creatures/{creature_id}/modules/{module_type}/{name}/options"
)
async def get_module_options(
    session_id: str,
    creature_id: str,
    module_type: str,
    name: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.get_module_options(cid, module_type, name)
    except KeyError:
        raise HTTPException(
            404,
            f"module {module_type!r}/{name!r} not found on "
            f"creature {creature_id!r}",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put(
    "/{session_id}/creatures/{creature_id}/modules/{module_type}/{name}/options"
)
async def set_module_options(
    session_id: str,
    creature_id: str,
    module_type: str,
    name: str,
    req: ModuleOptionsRequest,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        applied = await service.set_module_options(
            cid, module_type, name, req.values or {}
        )
    except KeyError:
        raise HTTPException(
            404,
            f"module {module_type!r}/{name!r} not found on "
            f"creature {creature_id!r}",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "status": "saved",
        "type": module_type,
        "name": name,
        "options": applied,
    }


@router.post(
    "/{session_id}/creatures/{creature_id}/modules/{module_type}/{name}/toggle"
)
async def toggle_module(
    session_id: str,
    creature_id: str,
    module_type: str,
    name: str,
    service: TerrariumService = Depends(get_service),
):
    cid = await resolve_creature_id(service, creature_id, session_id)
    try:
        return await service.toggle_module(cid, module_type, name)
    except KeyError:
        raise HTTPException(
            404,
            f"module {module_type!r}/{name!r} not found on "
            f"creature {creature_id!r}",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
