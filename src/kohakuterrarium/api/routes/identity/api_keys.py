"""Identity API keys — provider key CRUD."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kohakuterrarium.studio.identity.api_keys import (
    list_keys_payload,
    remove_key,
    set_key,
)

router = APIRouter()


class ApiKeyRequest(BaseModel):
    provider: str
    key: str


@router.get("/keys")
async def get_keys():
    return {"providers": list_keys_payload()}


@router.post("/keys")
async def set_key_route(req: ApiKeyRequest):
    try:
        set_key(req.provider, req.key)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return {"status": "saved", "provider": req.provider}


@router.delete("/keys/{provider}")
async def remove_key_route(provider: str):
    try:
        remove_key(provider)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    return {"status": "removed", "provider": provider}
