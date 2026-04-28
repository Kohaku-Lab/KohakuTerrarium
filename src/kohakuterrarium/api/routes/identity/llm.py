"""Identity LLM — backends, profiles, default model, native tools."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from kohakuterrarium.studio.identity.llm_backends import (
    list_backends,
    remove_backend,
    save_backend_record,
)
from kohakuterrarium.studio.identity.llm_default import (
    get_default,
    list_all_models_combined,
    set_default,
)
from kohakuterrarium.studio.identity.llm_native_tools import list_native_tools
from kohakuterrarium.studio.identity.llm_profiles import (
    list_profiles_payload,
    remove_profile,
    save_profile_record,
)

router = APIRouter()


class BackendRequest(BaseModel):
    name: str
    backend_type: str = "openai"
    base_url: str = ""
    api_key_env: str = ""
    provider_name: str = ""
    provider_native_tools: list[str] = Field(default_factory=list)


class ProfileRequest(BaseModel):
    name: str
    model: str
    provider: str = ""
    max_context: int = 128000
    max_output: int = 16384
    temperature: float | None = None
    reasoning_effort: str = ""
    service_tier: str = ""
    extra_body: dict | None = None
    variation_groups: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)


class DefaultModelRequest(BaseModel):
    name: str


@router.get("/backends")
async def get_backends():
    return {"backends": list_backends()}


@router.post("/backends")
async def create_backend(req: BackendRequest):
    try:
        save_backend_record(
            name=req.name,
            backend_type=req.backend_type,
            base_url=req.base_url,
            api_key_env=req.api_key_env,
            provider_name=req.provider_name,
            provider_native_tools=list(req.provider_native_tools or []),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "saved", "name": req.name}


@router.delete("/backends/{name}")
async def delete_backend_route(name: str):
    try:
        deleted = remove_backend(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not deleted:
        raise HTTPException(404, f"Provider not found: {name}")
    return {"status": "deleted", "name": name}


@router.get("/native-tools")
async def get_native_tools():
    """Return metadata for every provider-native built-in tool."""
    return {"tools": list_native_tools()}


@router.get("/profiles")
async def get_profiles():
    return {"profiles": list_profiles_payload()}


@router.post("/profiles")
async def create_profile(req: ProfileRequest):
    try:
        save_profile_record(
            name=req.name,
            model=req.model,
            provider=req.provider,
            max_context=req.max_context,
            max_output=req.max_output,
            temperature=req.temperature,
            reasoning_effort=req.reasoning_effort,
            service_tier=req.service_tier,
            extra_body=req.extra_body or {},
            variation_groups=req.variation_groups or {},
        )
    except ValueError as e:
        msg = str(e)
        if msg.startswith("Provider not found"):
            raise HTTPException(404, msg) from e
        raise HTTPException(400, msg) from e
    return {"status": "saved", "name": req.name, "provider": req.provider}


@router.delete("/profiles/{provider}/{name}")
async def delete_profile_route(provider: str, name: str):
    if not remove_profile(name, provider):
        raise HTTPException(404, f"Profile not found: {provider}/{name}")
    return {"status": "deleted", "name": name, "provider": provider}


@router.get("/default-model")
async def get_default_model_route():
    return {"default_model": get_default()}


@router.post("/default-model")
async def set_default_model_route(req: DefaultModelRequest):
    set_default(req.name)
    return {"status": "set", "default_model": req.name}


@router.get("/models")
async def get_all_models_route():
    return list_all_models_combined()
