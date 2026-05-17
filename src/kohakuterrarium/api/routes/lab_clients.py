"""``/api/lab/clients/*`` and ``/api/lab/pairing-tokens/*`` — per-client
control surface for the operator's Sites tab.

In standalone mode every route returns 404 (no host engine to act on).
In lab-host mode each route reaches into ``app.state.lab_host_engine``
and either evicts a client, updates the host's in-memory blocklist, or
rotates the shared pairing token.

The "block" verb updates HostEngine's in-memory blocklist so future
handshakes for that client id are rejected. The blocklist does not
persist across daemon restarts on purpose — operators expecting
permanent bans should rotate the pairing token instead.
"""

import secrets
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


class BlockReason(BaseModel):
    reason: str | None = None


class RotateResult(BaseModel):
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
    """Evict a single client from the lab cluster.

    Equivalent of the operator running ``kt kick <node>`` on the host
    (no such CLI command exists; this is the only surface today).
    """
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
    """Add ``node_id`` to the in-memory blocklist and evict if connected.

    HostEngine's accept loop checks its in-memory blocklist during
    handshakes; operators that need permanent bans should rotate the
    pairing token.
    """
    host = _require_lab_host(request)
    blocklist = getattr(request.app.state, "lab_blocklist", None)
    if blocklist is None:
        blocklist = set()
        request.app.state.lab_blocklist = blocklist
    blocklist.add(node_id)
    if hasattr(host, "block_client_id"):
        host.block_client_id(node_id)
    # Evict if currently connected.
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
    host = _require_lab_host(request)
    blocklist = set(getattr(request.app.state, "lab_blocklist", None) or set())
    if hasattr(host, "blocked_clients"):
        blocklist.update(host.blocked_clients())
    return {"blocked": sorted(blocklist)}


@router.post("/pairing-tokens/rotate", response_model=RotateResult)
async def rotate_pairing_token(request: Request) -> RotateResult:
    """Generate a fresh shared pairing token and install it on the host.

    Existing connected clients are NOT evicted — they keep their
    session. Only NEW joins must present the new token.
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
