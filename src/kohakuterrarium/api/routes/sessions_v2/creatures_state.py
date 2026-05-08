"""Per-creature state routes — scratchpad / triggers / env / system
prompt / working dir / native tool options.

Every read goes through ``asyncio.to_thread``: each reads from the
agent's scratchpad / config / session store, all of which are sync
data structures (some backed by SQLite). Frontend panels poll these
on every tab switch and on a 5 s cadence, so any blocking read here
stalls every other in-flight request for the whole panel refresh
window.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.sessions import creature_state

router = APIRouter()


class ScratchpadPatch(BaseModel):
    updates: dict[str, str | None]


class WorkingDirRequest(BaseModel):
    path: str


class NativeToolOptionsRequest(BaseModel):
    tool: str
    values: dict[str, Any] = {}


@router.get("/{session_id}/creatures/{creature_id}/scratchpad")
async def get_scratchpad(session_id: str, creature_id: str, engine=Depends(get_engine)):
    try:
        return await asyncio.to_thread(
            creature_state.get_scratchpad, engine, session_id, creature_id
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.patch("/{session_id}/creatures/{creature_id}/scratchpad")
async def patch_scratchpad(
    session_id: str,
    creature_id: str,
    req: ScratchpadPatch,
    engine=Depends(get_engine),
):
    try:
        return await asyncio.to_thread(
            creature_state.patch_scratchpad,
            engine,
            session_id,
            creature_id,
            req.updates,
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{session_id}/creatures/{creature_id}/triggers")
async def list_triggers(session_id: str, creature_id: str, engine=Depends(get_engine)):
    try:
        return await asyncio.to_thread(
            creature_state.list_triggers, engine, session_id, creature_id
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/env")
async def get_env(session_id: str, creature_id: str, engine=Depends(get_engine)):
    try:
        return await asyncio.to_thread(
            creature_state.get_env, engine, session_id, creature_id
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/system-prompt")
async def get_system_prompt(
    session_id: str, creature_id: str, engine=Depends(get_engine)
):
    try:
        return await asyncio.to_thread(
            creature_state.get_system_prompt, engine, session_id, creature_id
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.get("/{session_id}/creatures/{creature_id}/working-dir")
async def get_working_dir(
    session_id: str, creature_id: str, engine=Depends(get_engine)
):
    try:
        pwd = await asyncio.to_thread(
            creature_state.get_working_dir, engine, session_id, creature_id
        )
        return {"pwd": pwd}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.put("/{session_id}/creatures/{creature_id}/working-dir")
async def set_working_dir(
    session_id: str,
    creature_id: str,
    req: WorkingDirRequest,
    engine=Depends(get_engine),
):
    try:
        applied = await asyncio.to_thread(
            creature_state.set_working_dir, engine, session_id, creature_id, req.path
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "saved", "pwd": applied}


@router.get("/{session_id}/creatures/{creature_id}/native-tool-options")
async def get_native_tool_options(
    session_id: str, creature_id: str, engine=Depends(get_engine)
):
    try:
        tools = await asyncio.to_thread(
            creature_state.native_tool_inventory, engine, session_id, creature_id
        )
        return {"tools": tools}
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")


@router.put("/{session_id}/creatures/{creature_id}/native-tool-options")
async def set_native_tool_options(
    session_id: str,
    creature_id: str,
    req: NativeToolOptionsRequest,
    engine=Depends(get_engine),
):
    try:
        applied = await asyncio.to_thread(
            creature_state.set_native_tool_options,
            engine,
            session_id,
            creature_id,
            req.tool,
            req.values or {},
        )
    except KeyError:
        raise HTTPException(404, f"creature {creature_id!r} not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "saved", "tool": req.tool, "values": applied}
