"""Expose node-targeted Codex OAuth, usage, and reset-credit operations.

Worker requests execute on the selected worker because Codex OAuth credentials
are process-local and cannot be reused from the host. Streaming login is
host-only because cross-node event streaming is not available.
"""

import asyncio
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.identity.node_routing import (
    call_node_identity,
    is_host_target,
)
from kohakuterrarium.studio.identity.codex_oauth import (
    consume_reset_credit_async,
    get_status,
    get_usage_async,
    login_async,
)
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()

# Device-code polling can leave the NDJSON stream idle long enough for mobile
# clients or NATs to drop it. Periodic frames keep the transport alive; the
# module-level value also permits shorter test intervals.
HEARTBEAT_INTERVAL = 15.0


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
    # Worker login waits for interactive OAuth completion, so it requires a
    # longer cross-node request timeout than ordinary identity operations.
    return await call_node_identity(service, node, "codex_login", timeout=300.0)


@router.post(
    "/codex-login-stream",
    dependencies=[Depends(verify_admin_token)],
)
async def codex_login_stream(node: str = ""):
    """Run host Codex login as a line-delimited JSON event stream.

    The stream emits device-code details, periodic keepalive pings, and one
    terminal completion or error event. Worker login is rejected because the
    node-routing layer cannot relay incremental events.
    """
    if not is_host_target(node):
        raise HTTPException(
            400,
            (
                "Streaming Codex login is only supported on the host node "
                '(node=""); for worker-side login use POST /codex-login.'
            ),
        )

    queue: asyncio.Queue = asyncio.Queue()

    async def emit_device_code(verification_url: str, user_code: str, expires_in: int):
        """Queue device-code details before polling begins."""
        await queue.put(
            {
                "event": "device_code",
                "verification_url": verification_url,
                "user_code": user_code,
                "expires_in": expires_in,
            }
        )

    async def run_login():
        """Produce login events and terminate the queue with a sentinel."""
        try:
            # The frontend modal is the interaction surface, so opening a system
            # browser is redundant and can block on platforms without one. The
            # redirect listener still supports manually opening the auth URL.
            result = await login_async(
                on_device_code=emit_device_code, open_browser=False
            )
            await queue.put(
                {
                    "event": "completed",
                    "expires_at": result.get("expires_at"),
                }
            )
        except Exception as exc:
            await queue.put(
                {"event": "error", "message": f"{type(exc).__name__}: {exc}"}
            )
        finally:
            # A sentinel closes the consumer after every terminal outcome.
            await queue.put(None)

    async def stream():
        """Yield queued events and cancel login if the client disconnects."""
        task = asyncio.create_task(run_login())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_INTERVAL
                    )
                except asyncio.TimeoutError:
                    # Ping events carry no state; they only prevent idle
                    # transport timeouts during device-code polling.
                    yield json.dumps({"event": "ping"}) + "\n"
                    continue
                if event is None:
                    break
                yield json.dumps(event) + "\n"
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.get("/codex-status")
async def codex_status(
    node: str = "",
    service: TerrariumService = Depends(get_service),
):
    """Return Codex authentication status from the targeted node."""
    if is_host_target(node):
        return get_status()
    return await call_node_identity(service, node, "codex_status")


@router.get("/codex-usage")
async def get_codex_usage(
    node: str = "",
    service: TerrariumService = Depends(get_service),
):
    """Return live Codex rate limits and reset credits for the target node.

    Host requests fetch usage directly; worker requests execute against that
    worker's own credentials.
    """
    if is_host_target(node):
        try:
            return await get_usage_async()
        except Exception as e:
            raise HTTPException(401, f"Failed to refresh Codex tokens: {e}") from e
    return await call_node_identity(service, node, "codex_usage")


class CodexResetConsumeRequest(BaseModel):
    """Identify an optional idempotent Codex reset-credit redemption."""

    idempotency_key: str | None = None
    credit_id: str | None = None


@router.post("/codex-reset-consume", dependencies=[Depends(verify_admin_token)])
async def codex_reset_consume(
    req: CodexResetConsumeRequest,
    node: str = "",
    service: TerrariumService = Depends(get_service),
):
    """Redeem a Codex reset credit on the targeted node.

    The response does not mutate cached usage optimistically; callers must
    refetch authoritative usage after reset or already-redeemed outcomes.
    """
    if is_host_target(node):
        try:
            return await consume_reset_credit_async(
                idempotency_key=req.idempotency_key or None,
                credit_id=req.credit_id or None,
            )
        except PermissionError as e:
            raise HTTPException(401, str(e)) from e
        except httpx.HTTPStatusError as e:
            # Preserve upstream authentication failures as 401; other upstream
            # failures represent gateway or transport errors.
            if e.response.status_code == 401:
                raise HTTPException(401, f"Codex rejected the request: {e}") from e
            raise HTTPException(502, f"Codex reset consume failed: {e}") from e
        except Exception as e:
            raise HTTPException(502, f"Codex reset consume failed: {e}") from e
    return await call_node_identity(
        service,
        node,
        "codex_reset_consume",
        {
            "idempotency_key": req.idempotency_key or "",
            "credit_id": req.credit_id or "",
        },
    )
