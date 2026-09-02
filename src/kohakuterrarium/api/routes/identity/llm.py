"""Manage LLM backends, profiles, model defaults, and native-tool metadata."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.studio.identity.llm_backends import (
    list_backends,
    remove_backend,
    save_backend_record,
)
from kohakuterrarium.studio.identity.llm_default import (
    get_default,
    list_all_models_combined,
    resolve_and_set_default,
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
    """Describe a named LLM backend and its provider capabilities."""

    name: str
    backend_type: str = "openai"
    base_url: str = ""
    api_key_env: str = ""
    provider_name: str = ""
    provider_native_tools: list[str] = Field(default_factory=list)


class ProfileRequest(BaseModel):
    """Describe a model profile and its generation defaults."""

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
    """Select the profile used as the default model.

    ``name`` accepts ``provider/name`` or a bare preset name that resolves
    to exactly one preset; an empty string clears the default.
    """

    name: str


@router.get("/backends")
async def get_backends():
    """Return all configured LLM backends."""
    return {"backends": list_backends()}


@router.post("/backends", dependencies=[Depends(verify_admin_token)])
async def create_backend(req: BackendRequest):
    """Validate and persist an LLM backend record."""
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


@router.delete("/backends/{name}", dependencies=[Depends(verify_admin_token)])
async def delete_backend_route(name: str):
    """Delete an LLM backend unless configuration invariants reject it."""
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
    """Return all configured model profiles in API form."""
    return {"profiles": list_profiles_payload()}


@router.post("/profiles", dependencies=[Depends(verify_admin_token)])
async def create_profile(req: ProfileRequest):
    """Validate and persist a model profile for an existing provider."""
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


@router.delete(
    "/profiles/{provider}/{name}", dependencies=[Depends(verify_admin_token)]
)
async def delete_profile_route(provider: str, name: str):
    """Delete a provider-qualified model profile."""
    if not remove_profile(name, provider):
        raise HTTPException(404, f"Profile not found: {provider}/{name}")
    return {"status": "deleted", "name": name, "provider": provider}


@router.get("/default-model")
async def get_default_model_route():
    """Return the configured default model profile name."""
    return {"default_model": get_default()}


@router.post("/default-model", dependencies=[Depends(verify_admin_token)])
async def set_default_model_route(req: DefaultModelRequest):
    """Persist the default model, resolving the request to ``provider/name``.

    An empty name clears the default; anything else must resolve to exactly
    one preset, so a bare name shared by several providers is rejected
    instead of silently binding to the wrong one.
    """
    if not req.name:
        set_default("")
        return {"status": "set", "default_model": ""}
    identifier, error = resolve_and_set_default(req.name)
    if error:
        if error.startswith("Preset not found"):
            raise HTTPException(404, error)
        raise HTTPException(400, error)
    return {"status": "set", "default_model": identifier}


@router.get("/models")
async def get_all_models_route():
    """Return the combined model catalog across configured backends."""
    return list_all_models_combined()
