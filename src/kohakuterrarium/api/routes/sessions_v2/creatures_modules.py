"""Per-creature configurable-modules routes (unified across types).

All runtime configuration of plugins, provider-native tools, and any
future module type goes through this namespace. Replaces the
per-type ``/plugins/{name}/options`` and
``/native-tool-options`` endpoints for the runtime UI.

Routes:

* ``GET    /sessions/{sid}/creatures/{cid}/modules``                       inventory
* ``GET    /sessions/{sid}/creatures/{cid}/modules/{type}/{name}/options`` schema + values
* ``PUT    /sessions/{sid}/creatures/{cid}/modules/{type}/{name}/options`` apply
* ``POST   /sessions/{sid}/creatures/{cid}/modules/{type}/{name}/toggle``  enable/disable
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.sessions import creature_modules

router = APIRouter()


class ModuleOptionsRequest(BaseModel):
    values: dict[str, Any] = {}


@router.get("/{session_id}/creatures/{creature_id}/modules")
async def list_modules(session_id: str, creature_id: str, engine=Depends(get_engine)):
    try:
        modules = await asyncio.to_thread(
            creature_modules.list_modules, engine, session_id, creature_id
        )
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
    engine=Depends(get_engine),
):
    try:
        return await asyncio.to_thread(
            creature_modules.get_module_options,
            engine,
            session_id,
            creature_id,
            module_type,
            name,
        )
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
    engine=Depends(get_engine),
):
    try:
        applied = await asyncio.to_thread(
            creature_modules.set_module_options,
            engine,
            session_id,
            creature_id,
            module_type,
            name,
            req.values or {},
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
    engine=Depends(get_engine),
):
    try:
        return await creature_modules.toggle_module(
            engine, session_id, creature_id, module_type, name
        )
    except KeyError:
        raise HTTPException(
            404,
            f"module {module_type!r}/{name!r} not found on "
            f"creature {creature_id!r}",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
