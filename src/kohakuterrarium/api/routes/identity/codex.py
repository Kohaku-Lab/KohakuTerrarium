"""Identity codex — OAuth login/status/usage."""

from fastapi import APIRouter, HTTPException

from kohakuterrarium.studio.identity.codex_oauth import (
    get_status,
    get_usage_async,
    login_async,
)

router = APIRouter()


@router.post("/codex-login")
async def codex_login():
    """Run the Codex OAuth flow server-side (server must be local)."""
    try:
        return await login_async()
    except Exception as e:
        raise HTTPException(500, f"Codex login failed: {e}") from e


@router.get("/codex-status")
async def codex_status():
    return get_status()


@router.get("/codex-usage")
async def get_codex_usage():
    """Return the most-recent captured Codex rate-limit / credits snapshot."""
    try:
        return await get_usage_async()
    except Exception as e:
        raise HTTPException(401, f"Failed to refresh Codex tokens: {e}") from e
