"""Identity API keys — provider key CRUD.

Accepts a ``?node=<id>`` query param: when set to a connected worker,
the operation runs against THAT worker's local api_keys.yaml via Lab
APP (so the worker can have its OWN keys, independent of the host).
``node`` unset or ``_host`` keeps the original host-local behaviour.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.identity.node_routing import (
    call_node_identity,
    is_host_target,
)
from kohakuterrarium.studio.identity.api_keys import (
    list_keys_payload,
    remove_key,
    set_key,
)
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


class ApiKeyRequest(BaseModel):
    provider: str
    key: str


@router.get("/keys")
async def get_keys(node: str = "", service: TerrariumService = Depends(get_service)):
    if is_host_target(node):
        return {"providers": list_keys_payload()}
    resp = await call_node_identity(service, node, "list_keys")
    return {"providers": resp.get("providers") or []}


@router.post("/keys")
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
        return {"status": "saved", "provider": req.provider}
    return await call_node_identity(
        service,
        node,
        "save_key",
        {"provider": req.provider, "key": req.key},
    )


@router.delete("/keys/{provider}")
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
        return {"status": "removed", "provider": provider}
    return await call_node_identity(
        service,
        node,
        "remove_key",
        {"provider": provider},
    )
