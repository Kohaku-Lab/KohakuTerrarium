"""Complete WebSocket authentication subprotocol negotiation.

Host-token validation already occurs in ASGI middleware. Browser clients that offer
an auth-bearing subprotocol require the server to echo one selected value during
acceptance; otherwise they treat the upgrade as failed. This helper performs only
that negotiation and does not duplicate credential validation.
"""

from fastapi import WebSocket

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


_AUTH_SUBPROTOCOL_PREFIXES: tuple[str, ...] = ("kt-token.", "kt-session.")


def _pick_auth_subprotocol(websocket: WebSocket) -> str | None:
    """Return the first offered KT auth subprotocol, ignoring unrelated protocols.

    Missing or nonstandard header interfaces fall back to ordinary acceptance.
    """
    headers = getattr(websocket, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("sec-websocket-protocol", "")
    except (AttributeError, TypeError):
        return None
    if not raw:
        return None
    for part in raw.split(","):
        stripped = part.strip()
        if stripped.startswith(_AUTH_SUBPROTOCOL_PREFIXES):
            return stripped
    return None


async def accept_with_auth_echo(websocket: WebSocket) -> None:
    """Accept the WebSocket and echo an offered auth subprotocol when required."""
    chosen = _pick_auth_subprotocol(websocket)
    if chosen is None:
        await websocket.accept()
        return
    await websocket.accept(subprotocol=chosen)


__all__ = ["accept_with_auth_echo"]
