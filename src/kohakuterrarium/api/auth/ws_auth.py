"""WebSocket-side auth glue.

L2 (host token) is gated at the ASGI middleware layer
(:class:`HostTokenMiddleware`), so by the time a route handler is
invoked the request has already passed the host-token check.  This
module's job is the *handshake polish* that the middleware can't
cleanly do on its own:

- **Sub-protocol echo.**  When a browser client sends
  ``Sec-WebSocket-Protocol: kt-token.<value>``, RFC 6455 requires the
  server to either pick one of the offered protocols or omit the
  header.  Chromium / Firefox close the connection if neither happens;
  empty selection is treated as "negotiation failed."  We echo the
  matched auth sub-protocol back on ``accept`` so browser clients
  stay connected.

- **L4 user resolution** (Phase E).  After accept, ``current_ws_user``
  returns the authenticated :class:`User` (when L4 enabled) by parsing
  the same sub-protocol / query / cookie shapes the HTTP path supports.

The helper does NOT re-check the host token — the middleware already
did, and a second check would either drift or duplicate constant-time
compare work for no benefit.
"""

from fastapi import WebSocket

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


_AUTH_SUBPROTOCOL_PREFIXES: tuple[str, ...] = ("kt-token.", "kt-session.")


def _pick_auth_subprotocol(websocket: WebSocket) -> str | None:
    """Return the first KT auth sub-protocol the client offered, if any.

    Reads ``Sec-WebSocket-Protocol`` as a comma-separated list (Starlette
    normalises multi-header values into the same field).  Non-auth
    sub-protocols are ignored — we only echo entries that start with
    ``kt-token.`` or ``kt-session.`` because those carry the credential
    bearer we want to confirm.

    Defensive: when a non-Starlette WebSocket-shaped object is passed
    (test mocks that don't implement ``headers``), returns ``None`` so
    the caller falls through to a plain ``accept()``.
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
    """Drop-in replacement for ``await websocket.accept()``.

    Echoes the auth sub-protocol back when the client offered one,
    so browser clients (Chromium / Firefox) accept the negotiated
    upgrade.  Falls through to a plain ``accept()`` when no auth
    sub-protocol was requested — covers the query-token + no-auth
    paths uniformly.
    """
    chosen = _pick_auth_subprotocol(websocket)
    if chosen is None:
        await websocket.accept()
        return
    await websocket.accept(subprotocol=chosen)


__all__ = ["accept_with_auth_echo"]
