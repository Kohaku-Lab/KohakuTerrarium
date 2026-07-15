"""Manage node-local provider API keys and refresh live host credentials.

A worker target reads or mutates that worker's own key file, keeping credentials
independent across nodes. Host mutations notify every live local provider so
rotated or fallback credentials can take effect without restarting creatures.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.identity.node_routing import (
    call_node_identity,
    is_host_target,
)
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.identity.api_keys import (
    list_keys_payload,
    remove_key,
    set_key,
)
from kohakuterrarium.terrarium.service import TerrariumService
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _reload_provider_credentials(service: TerrariumService) -> int:
    """Reload credentials for every host-local provider and count changes.

    Failures are isolated per creature so one provider cannot prevent others
    from refreshing. Lab hosts without local creatures return zero; worker
    mutations are handled on the worker itself.
    """
    engine = host_engine_or_none(service)
    if engine is None:
        return 0
    rotated = 0
    for creature in engine.list_creatures():
        llm = getattr(getattr(creature, "agent", None), "llm", None)
        if llm is None:
            continue
        try:
            if llm.reload_credentials():
                rotated += 1
        except Exception as e:  # pragma: no cover - defensive
            logger.exception(
                "creature llm reload_credentials raised",
                creature_id=creature.creature_id,
                error=str(e),
            )
    return rotated


class ApiKeyRequest(BaseModel):
    """Carry a provider name and replacement API key."""

    provider: str
    key: str


@router.get("/keys")
async def get_keys(node: str = "", service: TerrariumService = Depends(get_service)):
    """List configured provider keys on the targeted node without exposing secrets."""
    if is_host_target(node):
        return {"providers": list_keys_payload()}
    resp = await call_node_identity(service, node, "list_keys")
    return {"providers": resp.get("providers") or []}


@router.post("/keys", dependencies=[Depends(verify_admin_token)])
async def set_key_route(
    req: ApiKeyRequest,
    node: str = "",
    service: TerrariumService = Depends(get_service),
):
    """Persist a provider key on the target and refresh affected live providers."""
    if is_host_target(node):
        try:
            set_key(req.provider, req.key)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        rotated = _reload_provider_credentials(service)
        return {"status": "saved", "provider": req.provider, "rotated": rotated}
    return await call_node_identity(
        service,
        node,
        "save_key",
        {"provider": req.provider, "key": req.key},
    )


@router.delete("/keys/{provider}", dependencies=[Depends(verify_admin_token)])
async def remove_key_route(
    provider: str,
    node: str = "",
    service: TerrariumService = Depends(get_service),
):
    """Remove a provider key on the target and refresh credential fallbacks."""
    if is_host_target(node):
        try:
            remove_key(provider)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        # Providers may retain a cached value when no replacement exists, while
        # providers with an environment fallback can rotate immediately.
        rotated = _reload_provider_credentials(service)
        return {"status": "removed", "provider": provider, "rotated": rotated}
    return await call_node_identity(
        service,
        node,
        "remove_key",
        {"provider": provider},
    )
