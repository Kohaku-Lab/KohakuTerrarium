"""Identity codex — OAuth login/status/usage.

Accepts a ``?node=<id>`` query param: when set to a connected worker,
the OAuth flow runs ON THAT WORKER (browser opens on the worker's
machine, tokens land in the worker's ``<config_dir>/codex-auth.json``).
This is the ONLY sound way to use Codex from a worker — OAuth tokens
are process-bound so the host's token cannot be reused remotely.
"""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

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

# Heartbeat cadence for ``codex-login-stream``.  Without periodic
# writes the NDJSON stream goes silent for up to 15 minutes (the
# device-code poll window) and Android WebView / mobile browsers
# happily kill the connection mid-poll.  When the ``completed``
# event finally enqueues, it has nowhere to go and the modal stays
# open forever.  15s is a comfortable margin under typical mobile
# NAT idle timeouts (30–60s).  Exposed at module level so tests can
# monkeypatch it down to a sub-second value.
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
    # Worker-side login: long-running (waits for user OAuth callback or
    # device-code entry). Bump the lab-request timeout so the user has
    # time to complete the flow.
    return await call_node_identity(service, node, "codex_login", timeout=300.0)


@router.post(
    "/codex-login-stream",
    dependencies=[Depends(verify_admin_token)],
)
async def codex_login_stream(node: str = ""):
    """Run Codex login while streaming progress events to the client.

    The frontend ``CodexLoginModal`` consumes this endpoint as a
    line-delimited JSON stream (one JSON object per line).  Events:

      * ``{"event": "device_code", "verification_url": ..., "user_code": ..., "expires_in": int}``
        — fired as soon as the device-code branch obtains the user
        code, BEFORE the poll loop starts.  The modal renders this
        so the user can manually open the URL + enter the code on
        any device.
      * ``{"event": "completed", "expires_at": float}`` — final
        success.  Modal closes itself with a success toast.
      * ``{"event": "error", "message": str}`` — terminal failure.

    Only the host path streams events directly.  Worker-side login
    falls back to the existing one-shot ``/codex-login`` route since
    cross-node event streaming isn't wired in 1.5.0; this leaves the
    modal-based UX functional in the common case (local Codex login
    on the host's own machine) and the host-router lab path for the
    rare worker case.
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
        await queue.put(
            {
                "event": "device_code",
                "verification_url": verification_url,
                "user_code": user_code,
                "expires_in": expires_in,
            }
        )

    async def run_login():
        try:
            # ``open_browser=False`` — the frontend modal is already
            # the user's interaction surface.  Auto-popping a system
            # browser on the host machine (same machine in standalone
            # mode) is at best redundant; on Android Chaquopy it
            # blocks the event loop hunting for a non-existent
            # system browser.  The browser-redirect HTTP server on
            # :1455 still runs in parallel — codex-rs ships the same
            # dual-flow shape — so a user who manually clicks the
            # printed auth URL still completes via the browser path.
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
            await queue.put(None)  # sentinel — close the stream

    async def stream():
        task = asyncio.create_task(run_login())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_INTERVAL
                    )
                except asyncio.TimeoutError:
                    # Heartbeat — keeps the underlying TCP / WebView
                    # fetch alive during the long device-code poll.
                    # The frontend ignores ``ping`` events; their
                    # only job is to push bytes onto the wire.
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
