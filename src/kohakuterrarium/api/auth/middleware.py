"""L2 — host token middleware.

Single shared secret gating every ``/api/*`` HTTP request.  Empty
``auth.host_token`` = off (current behaviour).  Loopback bypass skips
the gate ONLY on L2 (L3 / L4 still enforced even on 127.0.0.1) so a
desktop app's local host doesn't nag for credentials that an attacker
with shell access could read off disk anyway.

WebSocket auth is in ``ws_auth.py`` — ASGI middleware on a WS request
runs BEFORE the upgrade and HTTPException 401s don't translate to a
clean ``close`` frame for browser clients, so the per-route handler
calls into the WS auth helper explicitly after ``accept()`` (sub-
protocol negotiation) or before (query-token early reject).

The middleware reads ``app.state.auth_config`` once per request — the
snapshot is frozen at boot in :func:`api.app.create_app` so middleware
decisions stay coherent within a request.
"""

import secrets
from typing import Any, Awaitable, Callable
from urllib.parse import unquote

from starlette.types import ASGIApp, Receive, Scope, Send

from kohakuterrarium.api.auth.config import AuthConfig, load_auth_config
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})

# Paths that BYPASS L2 unconditionally — the capabilities probe must
# be reachable before the client even knows what auth shape the host
# expects; ``/healthz`` and ``/readyz`` are probed by container
# orchestrators that have no way to learn a token.
_UNGATED_PREFIXES: tuple[str, ...] = (
    "/api/auth/capabilities",
    "/healthz",
    "/readyz",
)


# Path prefixes the gate APPLIES to.  Anything outside these
# prefixes is the static SPA / SPA-router fallback / robots.txt /
# favicon — none of which carry credentials and all of which must
# remain reachable so the operator can actually load the login page.
# The audit caught the previous "gate everything" behaviour as a
# release-blocker: a remote browser couldn't fetch the HTML/JS to
# even prompt for the host token.
_GATED_PREFIXES: tuple[str, ...] = ("/api/", "/ws/")


class HostTokenMiddleware:
    """Pure-ASGI middleware so it can gate both HTTP and WebSocket
    handshakes uniformly.

    On HTTP miss: 401 JSON body.  On WS miss: close with code 4401
    (custom, doc'd in the WS-auth doc page) — the browser sees the
    close and our JS layer can distinguish it from a regular disconnect.
    """

    def __init__(self, app: ASGIApp):
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only intercept HTTP + WebSocket; everything else (lifespan,
        # etc.) passes through.
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        cfg = _resolve_config(scope)
        if not cfg.host_token_enabled:
            # Gate off — pass through.
            await self._app(scope, receive, send)
            return

        # Outside the gated prefixes (`/api/` and `/ws/`) → pass.
        # The static SPA, SPA-router catch-all, favicon, and other
        # asset paths must remain reachable so a remote browser can
        # actually load the login page before it knows the token.
        path = scope.get("path", "")
        if not any(path.startswith(prefix) for prefix in _GATED_PREFIXES):
            await self._app(scope, receive, send)
            return

        # Ungated probe paths always pass (capabilities / health).
        if any(path.startswith(prefix) for prefix in _UNGATED_PREFIXES):
            await self._app(scope, receive, send)
            return

        # CORS preflight (HTTP OPTIONS) — never carries Authorization;
        # let it through so CORS can respond.  The eventual real
        # request (after a successful preflight) carries the Bearer
        # token and gets gated normally.
        if scope["type"] == "http" and scope.get("method", "").upper() == "OPTIONS":
            await self._app(scope, receive, send)
            return

        # Loopback bypass — only when ``loopback_bypass`` AND the
        # client is genuinely on loopback.  ``X-Forwarded-For`` is
        # NOT trusted: operators behind a reverse proxy explicitly
        # set ``loopback_bypass = false``.
        if cfg.loopback_bypass and _is_loopback_client(scope):
            await self._app(scope, receive, send)
            return

        # Auth check — extract Bearer from headers (HTTP) OR sub-protocol /
        # query (WebSocket).
        if scope["type"] == "http":
            supplied = _bearer_from_http_headers(scope)
        else:
            # WebSocket: sub-protocol first, query string fallback.
            supplied = _token_from_ws_handshake(scope)

        if not supplied or not _constant_time_match(supplied, cfg.host_token):
            await _reject(scope, send)
            return

        await self._app(scope, receive, send)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_config(scope: Scope) -> AuthConfig:
    """Pull ``AuthConfig`` off ``app.state``; load fresh if missing.

    Mirrors the dependency-injection fallback so unit tests without
    ``create_app``'s boot path still work.
    """
    app = scope.get("app")
    cached = getattr(getattr(app, "state", None), "auth_config", None)
    if isinstance(cached, AuthConfig):
        return cached
    return load_auth_config()


def _is_loopback_client(scope: Scope) -> bool:
    client = scope.get("client")
    if not client or not isinstance(client, (tuple, list)):
        return False
    host = client[0] if len(client) > 0 else ""
    return host in _LOOPBACK_HOSTS


def _bearer_from_http_headers(scope: Scope) -> str:
    """Extract the L2 host token from request headers.

    Preferred shape: ``X-KT-Host-Token: <token>``.  Dedicated header
    so L2 (host gate) and L4 (user API token via
    ``Authorization: Bearer``) don't collide when both are enabled
    — a CLI / mobile / cross-origin caller can carry both at once:

        X-KT-Host-Token: <host_token>
        Authorization: Bearer <user_api_token>

    Backward-compatible fallback: ``Authorization: Bearer <token>``
    is still accepted as a host token when no ``X-KT-Host-Token`` is
    present.  This keeps the early-1.5.0 single-tenant deployments
    working unchanged.  When L4 is enabled AND the operator wants
    user-token auth alongside L2, the explicit header is required.
    """
    host_header = ""
    bearer = ""
    for raw_name, raw_value in scope.get("headers", []) or []:
        try:
            name = raw_name.decode("latin-1").lower()
        except (
            AttributeError,
            UnicodeDecodeError,
        ):  # pragma: no cover - ASGI server always gives bytes
            continue
        try:
            value = raw_value.decode("latin-1")
        except (
            AttributeError,
            UnicodeDecodeError,
        ):  # pragma: no cover - ASGI server always gives bytes
            continue
        if name == "x-kt-host-token":
            host_header = value.strip()
        elif name == "authorization" and not bearer:
            bearer = _parse_bearer(value)
    # Dedicated header wins.
    if host_header:
        return host_header
    return bearer


def _parse_bearer(header_value: str) -> str:
    """Return the token portion of ``Bearer <token>`` (or empty)."""
    parts = header_value.split(None, 1)
    if len(parts) != 2:
        return ""
    scheme, token = parts
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _token_from_ws_handshake(scope: Scope) -> str:
    """Sub-protocol first, query string fallback.

    Sub-protocol shape: ``Sec-WebSocket-Protocol: kt-token.<value>``.
    Query shape: ``?token=<value>``.

    The sub-protocol path is preferred because (a) the token doesn't
    land in HTTP access logs, and (b) it's structured — easier for
    intermediaries to drop the header on errors.  Query-token remains
    for CLI / curl-y clients that can't easily set sub-protocols.
    """
    # Sub-protocol — comma-separated values in the header.
    for raw_name, raw_value in scope.get("headers", []) or []:
        try:
            name = raw_name.decode("latin-1").lower()
        except (
            AttributeError,
            UnicodeDecodeError,
        ):  # pragma: no cover - ASGI server always gives bytes
            continue
        if name != "sec-websocket-protocol":
            continue
        try:
            value = raw_value.decode("latin-1")
        except (
            AttributeError,
            UnicodeDecodeError,
        ):  # pragma: no cover - ASGI server always gives bytes
            continue
        for part in value.split(","):
            stripped = part.strip()
            if stripped.startswith("kt-token."):
                return stripped[len("kt-token.") :]
    # Query string fallback.
    raw_query = scope.get("query_string", b"")
    try:
        query = (
            raw_query.decode("latin-1")
            if isinstance(raw_query, bytes)
            else str(raw_query)
        )
    except UnicodeDecodeError:  # pragma: no cover - latin-1 decodes any byte
        query = ""
    for pair in query.split("&"):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        if key == "token":
            # Lightweight URL-decode for the common case (%2B etc).
            try:
                return unquote(value).strip()
            except Exception:  # pragma: no cover - defensive
                return value.strip()
    return ""


def _constant_time_match(supplied: str, expected: str) -> bool:
    """Constant-time compare on UTF-8 encodings.

    Both inputs are short (typical token length 64 chars), so the
    comparison is fast even with the constant-time wrapper.
    """
    try:
        return secrets.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        )
    except (AttributeError, UnicodeEncodeError):  # pragma: no cover - defensive
        return False


async def _reject(scope: Scope, send: Send) -> None:
    """Send the auth-failure response.  HTTP → 401; WS → close 4401."""
    if scope["type"] == "http":
        body = b'{"error":"unauthorized","detail":"host token required"}'
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            # WWW-Authenticate so well-behaved clients prompt re-auth.
            (b"www-authenticate", b'Bearer realm="kohakuterrarium"'),
        ]
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})
    else:
        # WebSocket — must send a close frame after the handshake is
        # rejected.  Per RFC 6455, an unauthenticated client gets 4401
        # (private application code range 4000-4999).
        await send(
            {"type": "websocket.close", "code": 4401, "reason": "host token required"}
        )


# Convenience re-export type so callers can declare the signature
# without importing starlette.types directly.
_ASGIApp = ASGIApp
_Receive = Receive
_Send = Send
_Awaitable = Awaitable
_Callable = Callable
_Any = Any


__all__ = ["HostTokenMiddleware"]
