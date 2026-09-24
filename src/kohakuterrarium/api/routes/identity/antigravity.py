"""Admin-only access to the local agy account without OAuth ownership."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.auth.dependencies import verify_admin_token
from kohakuterrarium.api.routes.identity.node_routing import is_host_target
from kohakuterrarium.llm.antigravity_auth import AntigravityError
from kohakuterrarium.studio.identity import antigravity

router = APIRouter(dependencies=[Depends(verify_admin_token)])


def require_host(node: str):
    if not is_host_target(node):
        raise HTTPException(400, "Antigravity: local_host_only")


@router.get("/antigravity-status")
async def status(node: str = ""):
    require_host(node)
    return await asyncio.to_thread(antigravity.get_status)


@router.post("/antigravity-refresh")
async def refresh(node: str = ""):
    require_host(node)
    try:
        return await antigravity.refresh_credentials()
    except AntigravityError as exc:
        raise HTTPException(409, exc.code) from None


@router.post("/antigravity-models")
async def models(node: str = ""):
    require_host(node)
    try:
        return await antigravity.get_models()
    except AntigravityError as exc:
        raise HTTPException(502, exc.code) from None


@router.get("/antigravity-usage")
async def usage(node: str = ""):
    if not is_host_target(node):
        return antigravity.empty_usage("unsupported")
    return await antigravity.get_usage()
