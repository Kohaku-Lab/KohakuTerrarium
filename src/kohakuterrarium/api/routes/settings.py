from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from kohakuterrarium.llm.codex_auth import CodexTokens, oauth_login, refresh_tokens
from kohakuterrarium.llm.codex_rate_limits import get_cached as get_cached_codex_usage
from kohakuterrarium.llm.profiles import (
    LLMBackend,
    LLMProfile,
    PROVIDER_KEY_MAP,
    _is_available,
    delete_backend,
    delete_profile,
    get_api_key,
    get_default_model,
    list_all,
    list_api_keys,
    load_backends,
    load_profiles,
    save_api_key,
    save_backend,
    save_profile,
    set_default_model,
)

router = APIRouter()


class ApiKeyRequest(BaseModel):
    provider: str
    key: str


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


class BackendRequest(BaseModel):
    name: str
    backend_type: str = "openai"
    base_url: str = ""
    api_key_env: str = ""


class DefaultModelRequest(BaseModel):
    name: str


class UIPrefsUpdateRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


def _ui_prefs_path() -> Path:
    return Path.home() / ".kohakuterrarium" / "ui_prefs.json"


_UI_PREF_DEFAULTS: dict[str, Any] = {
    "theme": "system",
    "kt-desktop-zoom": 1.15,
    "kt-mobile-zoom": 1.25,
    "nav-expanded": True,
    "kt-force-desktop": False,
    "kt.presets.user": {},
    "kt.layout.activePreset": None,
    "kt.layout.trees": {},
    "kt.layout.instances": {},
    "kt.splitPane": {},
}


def _load_ui_prefs() -> dict[str, Any]:
    path = _ui_prefs_path()
    if not path.exists():
        return dict(_UI_PREF_DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(_UI_PREF_DEFAULTS)
        return {**_UI_PREF_DEFAULTS, **data}
    except Exception:
        return dict(_UI_PREF_DEFAULTS)


def _save_ui_prefs(values: dict[str, Any]) -> dict[str, Any]:
    merged = {**_load_ui_prefs(), **values}
    path = _ui_prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
    return merged


@router.get("/keys")
async def get_keys():
    masked = list_api_keys()
    entries = []
    for name, backend in load_backends().items():
        entries.append(
            {
                "provider": name,
                "backend_type": backend.backend_type,
                "env_var": backend.api_key_env,
                "has_key": bool(get_api_key(name)),
                "masked_key": masked.get(name, ""),
                "available": _is_available(name),
                "built_in": name in {"codex", *PROVIDER_KEY_MAP.keys()},
            }
        )
    return {"providers": entries}


@router.post("/keys")
async def set_key(req: ApiKeyRequest):
    if not req.provider or not req.key:
        raise HTTPException(400, "Provider and key are required")
    if req.provider not in load_backends():
        raise HTTPException(404, f"Provider not found: {req.provider}")
    save_api_key(req.provider, req.key)
    return {"status": "saved", "provider": req.provider}


@router.post("/codex-login")
async def codex_login():
    """Run the Codex OAuth flow server-side (server must be local)."""
    try:
        tokens = await oauth_login()
    except Exception as e:
        raise HTTPException(500, f"Codex login failed: {e}") from e
    return {"status": "ok", "expires_at": tokens.expires_at}


@router.get("/codex-status")
async def codex_status():
    tokens = CodexTokens.load()
    if not tokens:
        return {"authenticated": False}
    return {"authenticated": True, "expired": tokens.is_expired()}


@router.delete("/keys/{provider}")
async def remove_key(provider: str):
    if provider not in load_backends():
        raise HTTPException(404, f"Provider not found: {provider}")
    save_api_key(provider, "")
    return {"status": "removed", "provider": provider}


@router.get("/backends")
async def get_backends():
    built_in = {"codex", "openai", "openrouter", "anthropic", "gemini", "mimo"}
    return {
        "backends": [
            {
                "name": name,
                "backend_type": backend.backend_type,
                "base_url": backend.base_url or "",
                "api_key_env": backend.api_key_env or "",
                "built_in": name in built_in,
                "has_token": bool(get_api_key(name)),
                "available": _is_available(name),
            }
            for name, backend in load_backends().items()
        ]
    }


@router.post("/backends")
async def create_backend(req: BackendRequest):
    if not req.name or not req.backend_type:
        raise HTTPException(400, "Name and backend type are required")
    if req.backend_type not in {"openai", "codex", "anthropic"}:
        raise HTTPException(400, "Unsupported backend type")
    save_backend(
        LLMBackend(
            name=req.name,
            backend_type=req.backend_type,
            base_url=req.base_url or "",
            api_key_env=req.api_key_env or "",
        )
    )
    return {"status": "saved", "name": req.name}


@router.delete("/backends/{name}")
async def remove_backend(name: str):
    try:
        deleted = delete_backend(name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not deleted:
        raise HTTPException(404, f"Provider not found: {name}")
    return {"status": "deleted", "name": name}


@router.get("/profiles")
async def get_profiles():
    profiles = load_profiles()
    return {
        "profiles": [
            {
                "name": name,
                "model": p.model,
                "provider": p.provider,
                "backend_type": p.backend_type,
                "base_url": p.base_url or "",
                "api_key_env": p.api_key_env or "",
                "max_context": p.max_context,
                "max_output": p.max_output,
                "temperature": p.temperature,
                "reasoning_effort": p.reasoning_effort or "",
                "service_tier": p.service_tier or "",
                "extra_body": p.extra_body or {},
            }
            for name, p in profiles.items()
        ]
    }


@router.post("/profiles")
async def create_profile(req: ProfileRequest):
    if not req.name or not req.model or not req.provider:
        raise HTTPException(400, "Name, model, and provider are required")
    if req.provider not in load_backends():
        raise HTTPException(404, f"Provider not found: {req.provider}")
    profile = LLMProfile(
        name=req.name,
        model=req.model,
        provider=req.provider,
        max_context=req.max_context,
        max_output=req.max_output,
        temperature=req.temperature,
        reasoning_effort=req.reasoning_effort or "",
        service_tier=req.service_tier or "",
        extra_body=req.extra_body or {},
    )
    save_profile(profile)
    return {"status": "saved", "name": req.name}


@router.delete("/profiles/{name}")
async def remove_profile(name: str):
    if not delete_profile(name):
        raise HTTPException(404, f"Profile not found: {name}")
    return {"status": "deleted", "name": name}


@router.get("/default-model")
async def get_default():
    return {"default_model": get_default_model()}


@router.post("/default-model")
async def set_default(req: DefaultModelRequest):
    set_default_model(req.name)
    return {"status": "set", "default_model": req.name}


@router.get("/models")
async def get_all_models():
    return list_all()


@router.get("/ui-prefs")
async def get_ui_prefs():
    return {"values": _load_ui_prefs()}


@router.post("/ui-prefs")
async def update_ui_prefs(req: UIPrefsUpdateRequest):
    return {"values": _save_ui_prefs(req.values or {})}


class MCPServerRequest(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""


@router.get("/mcp")
async def list_mcp_servers():
    servers = _load_mcp_config()
    return {"servers": servers}


@router.post("/mcp")
async def add_mcp_server(req: MCPServerRequest):
    if not req.name:
        raise HTTPException(400, "Name is required")
    servers = _load_mcp_config()
    servers = [s for s in servers if s.get("name") != req.name]
    servers.append(req.model_dump())
    _save_mcp_config(servers)
    return {"status": "saved", "name": req.name}


@router.delete("/mcp/{name}")
async def remove_mcp_server(name: str):
    servers = _load_mcp_config()
    new_servers = [s for s in servers if s.get("name") != name]
    if len(new_servers) == len(servers):
        raise HTTPException(404, f"MCP server not found: {name}")
    _save_mcp_config(new_servers)
    return {"status": "removed", "name": name}


def _mcp_config_path():
    return Path.home() / ".kohakuterrarium" / "mcp_servers.yaml"


def _load_mcp_config():
    path = _mcp_config_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_mcp_config(servers):
    path = _mcp_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(servers, f, default_flow_style=False, sort_keys=False)


@router.get("/codex-usage")
async def get_codex_usage():
    """Return the most-recent captured Codex rate-limit / credits snapshot.

    The old ``https://chatgpt.com/backend-api/codex/usage`` endpoint has
    been removed upstream and is now shielded by Cloudflare (non-browser
    clients receive a managed-challenge HTML page). Per the current
    Codex implementation, rate-limit state rides on the response headers
    of every chat-completion request. ``codex_provider`` captures those
    headers into a process-level cache; this endpoint returns that cache.

    Response shape::

        {
          "status": "ok" | "no_data_yet" | "not_logged_in",
          "captured_at": <unix seconds | null>,
          "snapshots": [                  # one per rate-limit family
            {
              "limit_id": "codex",
              "limit_name": null,
              "primary":   {"used_percent": 12.5, "window_minutes": 300,  "resets_at": ...},
              "secondary": {"used_percent": 80.0, "window_minutes": 1440, "resets_at": ...},
              "credits":   {"has_credits": ..., "unlimited": ..., "balance": "..."},
              "plan_type": null,
              "rate_limit_reached_type": null
            }
          ],
          "promo_message": null | "<text>"
        }

    ``status="no_data_yet"`` means the user is logged in but hasn't made
    a Codex request this process-lifetime yet. Send one chat turn, then
    refetch.
    """
    tokens = CodexTokens.load()
    if not tokens:
        return {
            "status": "not_logged_in",
            "captured_at": None,
            "snapshots": [],
            "promo_message": None,
        }
    if tokens.is_expired():
        try:
            await refresh_tokens(tokens)
        except Exception as e:
            raise HTTPException(401, f"Failed to refresh Codex tokens: {e}") from e

    cached = get_cached_codex_usage()
    if cached is None or cached.is_empty():
        return {
            "status": "no_data_yet",
            "captured_at": None,
            "snapshots": [],
            "promo_message": None,
        }
    return {
        "status": "ok",
        "captured_at": cached.captured_at,
        "snapshots": [snap.to_dict() for snap in cached.snapshots],
        "promo_message": cached.promo_message,
    }
