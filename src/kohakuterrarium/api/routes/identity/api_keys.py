"""Identity API keys — provider key CRUD.

Accepts a ``?node=<id>`` query param: when set to a connected worker,
the operation runs against THAT worker's local api_keys.yaml via Lab
APP (so the worker can have its OWN keys, independent of the host).
``node`` unset or ``_host`` keeps the original host-local behaviour.

Live-reload: after a successful save / delete on the host target,
fan a no-await ``llm.reload_credentials()`` out to every live
creature's provider so the rotation takes effect on the next chat
request — no creature restart required. See
:meth:`kohakuterrarium.llm.base.BaseLLMProvider.reload_credentials`.
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
    """Fan ``reload_credentials`` out to every live creature.

    Returns the count of providers that actually rotated. Defensive
    per-creature ``try/except`` so a malformed agent doesn't block the
    others. Lab-host mode (``host_engine_or_none`` returns ``None``)
    no-ops — the host has no local creatures, and the worker save path
    already handled the key write on its own machine.
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
    provider: str
    key: str


@router.get("/keys")
async def get_keys(node: str = "", service: TerrariumService = Depends(get_service)):
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
    if is_host_target(node):
        try:
            remove_key(provider)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        # Removing a key clears the file entry; running providers keep
        # the old cached key until their next reload — which is fine
        # for the "I removed the key by mistake" workflow. We still
        # fan-out so providers whose key DID change (e.g. rotated to
        # env fallback) pick the new value up.
        rotated = _reload_provider_credentials(service)
        return {"status": "removed", "provider": provider, "rotated": rotated}
    return await call_node_identity(
        service,
        node,
        "remove_key",
        {"provider": provider},
    )
