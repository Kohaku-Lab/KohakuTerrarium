"""Where this machine keeps provider API keys: the key file or the system keychain."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from kohakuterrarium.api.auth.dependencies import verify_admin_token
from kohakuterrarium.api.routes._local import is_local, local_only
from kohakuterrarium.llm.profiles import secret_store
from kohakuterrarium.llm.profiles.api_keys import switch_secret_store

router = APIRouter()


class SecretStoreRequest(BaseModel):
    mode: str


def _status(request: Request) -> dict[str, Any]:
    return {
        "local": is_local(request),
        "mode": secret_store.configured_mode(),
        "active": secret_store.active(),
        "available": secret_store.keychain_available(),
        "backend": secret_store.backend_name(),
    }


@router.get("/secret-store")
async def get_secret_store(request: Request) -> dict[str, Any]:
    """Report the chosen key store and whether a system keychain can be used."""
    return _status(request)


@router.post("/secret-store", dependencies=[Depends(verify_admin_token)])
async def set_secret_store(
    request: Request, body: SecretStoreRequest
) -> dict[str, Any]:
    """Move every stored API key to the key file or the system keychain."""
    local_only(request, "the key store can only be changed from this machine")
    try:
        moved = switch_secret_store(body.mode)
    except secret_store.SecretStoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**_status(request), "moved": moved}
