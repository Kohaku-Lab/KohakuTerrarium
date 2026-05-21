"""Identity codex — OAuth login/status/usage.

Accepts a ``?node=<id>`` query param: when set to a connected worker,
the OAuth flow runs ON THAT WORKER (browser opens on the worker's
machine, tokens land in the worker's ``<config_dir>/codex-auth.json``).
This is the ONLY sound way to use Codex from a worker — OAuth tokens
are process-bound so the host's token cannot be reused remotely.
"""

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.identity.node_routing import (
    call_node_identity,
    is_host_target,
)
from kohakuterrarium.studio.identity.codex_oauth import (
    get_status,
    get_usage_async,
    login_async,
)
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


@router.post("/codex-login", dependencies=[Depends(verify_admin_token)])
async def codex_login(
    node: str = "",
    service: TerrariumService = Depends(get_service),
):
    """Run the Codex OAuth flow on the targeted node."""
    if is_host_target(node):
        try:
            return await login_async()
        except Exception as e:
            raise HTTPException(500, f"Codex login failed: {e}") from e
    # Worker-side login: long-running (waits for user OAuth callback or
    # device-code entry). Bump the lab-request timeout so the user has
    # time to complete the flow.
    return await call_node_identity(service, node, "codex_login", timeout=300.0)


@router.get("/codex-status")
async def codex_status(
    node: str = "",
    service: TerrariumService = Depends(get_service),
):
    if is_host_target(node):
        return get_status()
    return await call_node_identity(service, node, "codex_status")


@router.get("/codex-usage")
async def get_codex_usage():
    """Return the most-recent captured Codex rate-limit / credits snapshot."""
    try:
        return await get_usage_async()
    except Exception as e:
        raise HTTPException(401, f"Failed to refresh Codex tokens: {e}") from e
