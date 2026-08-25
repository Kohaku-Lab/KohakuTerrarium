"""Expose redacted node-local Grok subscription credential status."""

from fastapi import APIRouter, Depends

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.identity.node_routing import (
    call_node_identity,
    is_host_target,
)
from kohakuterrarium.studio.identity.grok_subscription import get_status
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


@router.get("/grok-status")
async def grok_status(
    node: str = "",
    service: TerrariumService = Depends(get_service),
):
    """Return Grok subscription status from the node that will execute calls."""
    if is_host_target(node):
        return get_status()
    return await call_node_identity(service, node, "grok_status")


__all__ = ["router"]
