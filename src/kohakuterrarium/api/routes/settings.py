"""Settings routes - API keys, custom model profiles, default model."""

import datetime

import httpx
import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kohakuterrarium.llm.codex_auth import CodexTokens, refresh_tokens

from kohakuterrarium.llm.profiles import (
    PROVIDER_KEY_MAP,
    LLMProfile,
    _is_available,
    delete_profile,
    get_api_key,
    get_default_model,
    list_api_keys,
    list_all,
    load_profiles,
    save_api_key,
    save_profile,
    set_default_model,
)

router = APIRouter()


# ── Request models ──


class ApiKeyRequest(BaseModel):
    provider: str
    key: str


class ProfileRequest(BaseModel):
    name: str
    model: str
    provider: str = "openai"
    base_url: str = ""
    api_key_env: str = ""
    max_context: int = 128000
    max_output: int = 16384
    temperature: float | None = None
    reasoning_effort: str = ""
    extra_body: dict | None = None


class DefaultModelRequest(BaseModel):
    name: str


# ── API keys ──


@router.get("/keys")
async def get_keys():
    """List stored API keys (masked) + availability status."""
    masked = list_api_keys()
    # Add provider list with status
    providers = []
    for provider, env_var in PROVIDER_KEY_MAP.items():
        providers.append(
            {
                "provider": provider,
                "env_var": env_var,
                "has_key": bool(get_api_key(provider)),
                "masked_key": masked.get(provider, ""),
                "available": _is_available(provider),
            }
        )
    # Add codex (OAuth-based)
    providers.insert(
        0,
        {
            "provider": "codex",
            "env_var": "",
            "has_key": _is_available("codex"),
            "masked_key": "OAuth" if _is_available("codex") else "",
            "available": _is_available("codex"),
        },
    )
    return {"providers": providers}


@router.post("/keys")
async def set_key(req: ApiKeyRequest):
    """Save an API key for a provider."""
    if not req.provider or not req.key:
        raise HTTPException(400, "Provider and key are required")
    save_api_key(req.provider, req.key)
    return {"status": "saved", "provider": req.provider}


@router.delete("/keys/{provider}")
async def remove_key(provider: str):
    """Remove a stored API key."""
    save_api_key(provider, "")
    return {"status": "removed", "provider": provider}


# ── Custom model profiles ──


@router.get("/profiles")
async def get_profiles():
    """List user-defined custom model profiles."""
    profiles = load_profiles()
    return {
        "profiles": [
            {
                "name": name,
                "model": p.model,
                "provider": p.provider,
                "base_url": p.base_url or "",
                "api_key_env": p.api_key_env or "",
                "max_context": p.max_context,
                "max_output": p.max_output,
                "temperature": p.temperature,
                "reasoning_effort": p.reasoning_effort or "",
                "extra_body": p.extra_body or {},
            }
            for name, p in profiles.items()
        ]
    }


@router.post("/profiles")
async def create_profile(req: ProfileRequest):
    """Create or update a custom model profile."""
    if not req.name or not req.model:
        raise HTTPException(400, "Name and model are required")
    profile = LLMProfile(
        name=req.name,
        model=req.model,
        provider=req.provider,
        base_url=req.base_url or None,
        api_key_env=req.api_key_env or None,
        max_context=req.max_context,
        max_output=req.max_output,
        temperature=req.temperature,
        reasoning_effort=req.reasoning_effort or None,
        extra_body=req.extra_body,
    )
    save_profile(profile)
    return {"status": "saved", "name": req.name}


@router.delete("/profiles/{name}")
async def remove_profile(name: str):
    """Delete a custom model profile."""
    if not delete_profile(name):
        raise HTTPException(404, f"Profile not found: {name}")
    return {"status": "deleted", "name": name}


# ── Default model ──


@router.get("/default-model")
async def get_default():
    """Get the current default model name."""
    return {"default_model": get_default_model()}


@router.post("/default-model")
async def set_default(req: DefaultModelRequest):
    """Set the default model."""
    set_default_model(req.name)
    return {"status": "set", "default_model": req.name}


# ── All models (convenience: same as /api/configs/models but here too) ──


@router.get("/models")
async def get_all_models():
    """List all available models (presets + user profiles) with status."""
    return list_all()


# ── MCP server configs (global, saved in ~/.kohakuterrarium/mcp_servers.yaml) ──


class MCPServerRequest(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""


@router.get("/mcp")
async def list_mcp_servers():
    """List globally configured MCP servers."""
    servers = _load_mcp_config()
    return {"servers": servers}


@router.post("/mcp")
async def add_mcp_server(req: MCPServerRequest):
    """Add or update a global MCP server config."""
    if not req.name:
        raise HTTPException(400, "Name is required")
    servers = _load_mcp_config()
    # Replace if exists, add if new
    servers = [s for s in servers if s.get("name") != req.name]
    servers.append(req.model_dump())
    _save_mcp_config(servers)
    return {"status": "saved", "name": req.name}


@router.delete("/mcp/{name}")
async def remove_mcp_server(name: str):
    """Remove a global MCP server config."""
    servers = _load_mcp_config()
    new_servers = [s for s in servers if s.get("name") != name]
    if len(new_servers) == len(servers):
        raise HTTPException(404, f"MCP server not found: {name}")
    _save_mcp_config(new_servers)
    return {"status": "removed", "name": name}


def _mcp_config_path():
    from pathlib import Path

    return Path.home() / ".kohakuterrarium" / "mcp_servers.yaml"


def _load_mcp_config() -> list[dict]:
    path = _mcp_config_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("servers", []) if isinstance(data, dict) else []
    except Exception as e:
        _ = e  # MCP config unreadable
        return []


def _save_mcp_config(servers: list[dict]) -> None:
    path = _mcp_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump({"servers": servers}, f, default_flow_style=False, sort_keys=False)


# ── Codex usage ──


@router.get("/codex-usage")
async def get_codex_usage() -> dict:
    """Fetch Codex quota/usage from chatgpt.com using stored OAuth token.

    Requires the user to be logged in via ``kt login codex``.
    Returns rate limit windows, credits, and plan info.
    """
    tokens = CodexTokens.load()
    if not tokens:
        raise HTTPException(
            status_code=401,
            detail="Not logged in with Codex. Run: kt login codex",
        )

    if tokens.is_expired():
        try:
            tokens = await refresh_tokens(tokens)
        except Exception as e:
            raise HTTPException(
                status_code=401,
                detail=f"Codex token expired and could not be refreshed: {e}",
            )

    url = "https://chatgpt.com/backend-api/wham/usage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {tokens.access_token}",
                    "User-Agent": "codex-cli",
                    "Content-Type": "application/json",
                },
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Request failed: {e}")

    if resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="Codex token rejected — re-login with: kt login codex",
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=resp.status_code, detail="Failed to fetch Codex usage"
        )

    data = resp.json()

    # Enrich windows with human-readable reset timestamps
    def _enrich_window(w: dict | None) -> dict | None:
        if not w:
            return None
        return {**w, "reset_at_iso": _unix_to_iso(w.get("reset_at", 0))}

    rl = data.get("rate_limit", {})
    return {
        "logged_in": True,
        "email": data.get("email", ""),
        "plan_type": data.get("plan_type", ""),
        "allowed": rl.get("allowed", True),
        "limit_reached": rl.get("limit_reached", False),
        "primary_window": _enrich_window(rl.get("primary_window")),
        "secondary_window": _enrich_window(rl.get("secondary_window")),
        "credits": data.get("credits"),
        "additional_rate_limits": data.get("additional_rate_limits", []),
        "spend_control": data.get("spend_control"),
    }


def _unix_to_iso(ts: float | int) -> str:
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()
