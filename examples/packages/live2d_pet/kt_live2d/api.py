"""Authenticated UI binding and bounded action delivery for a local companion."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from kohakuterrarium.api.ext import current_service, require_user
from kohakuterrarium.api.routes.sessions_v2._helpers import resolve_creature_id
from kohakuterrarium.terrarium.service.service import LocalTerrariumService

from .actions import ACTION_SETS, validate_action_set
from .bindings import attach, bindings, remove_binding, renew
from .control import is_live2d_tool
from .model_api import router as model_router
from .ownership import claim_controller, controller_view

router = APIRouter(dependencies=[Depends(require_user)])
router.include_router(model_router)


class ControllerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client: str = Field(min_length=1, max_length=80)
    operation: Literal["claim", "renew", "release", "takeover"]
    token: str = Field(default="", max_length=80)


@router.get("/controller")
async def controller(service=Depends(current_service)):
    if not isinstance(service, LocalTerrariumService):
        raise HTTPException(409, "Local creature required.")
    return {"controller": controller_view(service.engine)}


@router.post("/controller")
async def update_controller(req: ControllerRequest, service=Depends(current_service)):
    if not isinstance(service, LocalTerrariumService):
        raise HTTPException(409, "Local creature required.")
    try:
        value = claim_controller(service.engine, req.client, req.operation, req.token)
    except ValueError as exc:
        raise HTTPException(
            409, {"message": str(exc), "controller": controller_view(service.engine)}
        ) from exc
    return {"controller": value}


@router.get("/companions")
async def companions(service=Depends(current_service)):
    """List loaded, explicitly configured companions without creating agents."""
    if not isinstance(service, LocalTerrariumService):
        raise HTTPException(
            409, "Live2D control requires a local creature on this host."
        )
    rows = []
    for creature in service.engine.list_creatures():
        tool = creature.agent.registry.get_tool("l2d")
        if (
            is_live2d_tool(tool)
            and tool.purpose == "companion"
            and creature.status not in {"stopped", "killed"}
        ):
            rows.append(
                {
                    "sessionId": creature.graph_id,
                    "creatureId": creature.creature_id,
                    "label": creature.name or creature.config_name,
                }
            )
    return {"companions": rows}


class Binding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease: str = Field(min_length=1, max_length=80)
    sessionId: str = ""
    creatureId: str = ""
    enabled: bool = False
    renew: bool = False
    model: str = Field(default="dragon", max_length=64)
    waiting: bool = False
    mode: Literal["expression", "agent"] = "expression"
    actionSet: dict | None = None
    controller: str = Field(default="", max_length=80)


@router.post("/binding")
async def bind(req: Binding, service=Depends(current_service)):
    if not isinstance(service, LocalTerrariumService):
        raise HTTPException(
            409, "Live2D control requires a local creature on this host."
        )
    engine = service.engine
    owner = controller_view(engine)
    if (req.controller and not owner) or (owner and owner["token"] != req.controller):
        raise HTTPException(409, "Controller changed.")
    current = bindings.get(engine)
    if not req.enabled:
        if current and current.tool.lease == req.lease:
            remove_binding(engine)
        return {"enabled": False}
    if not req.sessionId or not req.creatureId:
        raise HTTPException(400, "Select a live conversation and creature first.")
    if req.model not in ACTION_SETS:
        raise HTTPException(400, "Unknown model adapter.")
    try:
        catalog = validate_action_set(
            req.actionSet if req.actionSet is not None else ACTION_SETS[req.model],
            req.model,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    cid = await resolve_creature_id(service, req.creatureId, req.sessionId)
    if controller_view(engine) != owner:
        raise HTTPException(409, "Controller changed.")
    agent = engine.get_creature(cid).agent
    current = bindings.get(engine)
    owns = current and current.agent is agent and current.tool.lease == req.lease
    if (current and not owns) or (req.renew and not owns):
        raise HTTPException(
            409, "Pet is controlled by another page or the lease expired."
        )
    try:
        attachment = attach(engine, agent, req.lease, req.mode)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    attachment.tool.bind(req.lease, req.model, req.waiting, catalog)
    renew(engine, attachment)
    return {"enabled": True, "creatureId": cid, "mode": req.mode}


@router.get("/events")
async def events(lease: str, service=Depends(current_service)):
    if not isinstance(service, LocalTerrariumService):
        raise HTTPException(409, "Local creature required.")
    current = bindings.get(service.engine)
    if not current or current.tool.lease != lease:
        raise HTTPException(409, "Binding changed.")
    tool = current.tool
    try:
        command = await asyncio.wait_for(tool.events.get(), timeout=15)
    except asyncio.TimeoutError:
        return {"command": None}
    if tool.lease != lease or bindings.get(service.engine) is not current:
        return {"command": None}
    return {"command": command}
