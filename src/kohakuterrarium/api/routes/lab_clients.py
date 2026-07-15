"""Control lab clients, the transient blocklist, and the pairing token.

These routes are available only when the API owns a lab host engine. Blocking
rejects future handshakes and evicts an active client, but the blocklist is
intentionally process-local; rotating the shared pairing token is the durable
way to invalidate access after a restart.
"""

import secrets
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


class BlockReason(BaseModel):
    """Describe the operator-supplied reason for blocking a client."""

    reason: str | None = None


class RotateResult(BaseModel):
    """Return a newly installed pairing token and its activation semantics."""

    token: str
    note: str


def _require_lab_host(request: Request) -> Any:
    """Return ``host_engine`` or raise 404 if not in lab-host mode."""
    host = getattr(request.app.state, "lab_host_engine", None)
    if host is None:
        raise HTTPException(404, "lab routes are only available in lab-host mode")
    return host


@router.post("/clients/{node_id}/disconnect")
async def disconnect_client(
    request: Request, node_id: str
) -> dict[str, Literal["ok"] | str]:
    """Evict a connected client without preventing a later reconnect."""
    host = _require_lab_host(request)
    clients = getattr(host, "_clients", {}) or {}
    client = clients.get(node_id)
    if client is None:
        raise HTTPException(404, f"Unknown / disconnected node: {node_id}")
    try:
        await host._disconnect_client(client, reason="operator-disconnect")
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("disconnect_client failed", node_id=node_id)
        raise HTTPException(500, f"disconnect failed: {e}") from e
    return {"status": "ok", "node_id": node_id}


@router.post("/clients/{node_id}/block")
async def block_client(
    request: Request, node_id: str, body: BlockReason
) -> dict[str, Any]:
    """Block future handshakes for ``node_id`` and evict it if connected.

    The block is process-local; rotating the pairing token is required to
    invalidate credentials across host restarts.
    """
    host = _require_lab_host(request)
    blocklist = getattr(request.app.state, "lab_blocklist", None)
    if blocklist is None:
        blocklist = set()
        request.app.state.lab_blocklist = blocklist
    blocklist.add(node_id)
    if hasattr(host, "block_client_id"):
        host.block_client_id(node_id)
    # A block must take effect immediately, not only on the next handshake.
    clients = getattr(host, "_clients", {}) or {}
    client = clients.get(node_id)
    if client is not None:
        try:
            await host._disconnect_client(client, reason="operator-block")
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("block disconnect failed", node_id=node_id)
            raise HTTPException(500, f"block disconnect failed: {e}") from e
    return {
        "status": "ok",
        "node_id": node_id,
        "reason": body.reason or "",
        "block_size": len(blocklist),
    }


@router.delete("/clients/blocklist/{node_id}")
async def unblock_client(request: Request, node_id: str) -> dict[str, Any]:
    """Remove ``node_id`` from the blocklist."""
    host = _require_lab_host(request)
    blocklist = getattr(request.app.state, "lab_blocklist", None)
    if blocklist is None:
        blocklist = set()
        request.app.state.lab_blocklist = blocklist
    blocklist.discard(node_id)
    if hasattr(host, "unblock_client_id"):
        host.unblock_client_id(node_id)
    return {"status": "ok", "node_id": node_id, "block_size": len(blocklist)}


@router.get("/clients/blocklist")
async def list_blocked(request: Request) -> dict[str, Any]:
    """Return the union of API-state and host-engine blocked client IDs."""
    host = _require_lab_host(request)
    blocklist = set(getattr(request.app.state, "lab_blocklist", None) or set())
    if hasattr(host, "blocked_clients"):
        blocklist.update(host.blocked_clients())
    return {"blocked": sorted(blocklist)}


@router.post("/pairing-tokens/rotate", response_model=RotateResult)
async def rotate_pairing_token(request: Request) -> RotateResult:
    """Install a fresh pairing token for new joins.

    Existing sessions remain connected; only subsequent handshakes must present
    the new token.
    """
    host = _require_lab_host(request)
    new_token = secrets.token_urlsafe(24)
    request.app.state.lab_token = new_token
    if hasattr(host, "set_token"):
        host.set_token(new_token)
    elif hasattr(host, "_config") and hasattr(host._config, "token"):
        try:
            host._config.token = new_token
        except Exception:  # pragma: no cover - defensive
            logger.exception("could not patch host config token")
    return RotateResult(
        token=new_token,
        note=(
            "New joins use this token; existing connections are unaffected. "
            "Share via a secure channel — do not paste in chat / commit logs."
        ),
    )


__all__ = ["router"]
