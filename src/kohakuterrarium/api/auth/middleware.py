"""Gate API and WebSocket traffic with the shared host token.

An empty token disables the gate. Static frontend assets and discovery probes remain
public so clients can load the authentication UI and determine host requirements.
Loopback bypass affects only this host-token layer; admin and user checks still apply.
"""

import secrets
from typing import Any, Awaitable, Callable
from urllib.parse import unquote

from starlette.types import ASGIApp, Receive, Scope, Send

from kohakuterrarium.api.auth.config import AuthConfig, load_auth_config
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})

# Clients need capabilities before they can authenticate, and orchestrator probes
# cannot participate in application-level token discovery.
_UNGATED_PREFIXES: tuple[str, ...] = (
    "/api/auth/capabilities",
    "/healthz",
    "/readyz",
)


# Static SPA resources stay public so a remote browser can load the login flow
# before it possesses a host token.
_GATED_PREFIXES: tuple[str, ...] = ("/api/", "/ws/")


class HostTokenMiddleware:
    """Apply one host-token policy to HTTP requests and WebSocket handshakes.

    HTTP failures return 401 JSON; WebSocket failures use private close code 4401 so
    clients can distinguish authentication failure from an ordinary disconnect.
    """

    def __init__(self, app: ASGIApp):
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Lifespan and other ASGI scopes do not carry application requests.
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return

        cfg = _resolve_config(scope)
        if not cfg.host_token_enabled:
            # An empty configured token explicitly disables host authentication.
            await self._app(scope, receive, send)
            return

        # Non-API assets remain reachable before the browser knows the token.
        path = scope.get("path", "")
        if not any(path.startswith(prefix) for prefix in _GATED_PREFIXES):
            await self._app(scope, receive, send)
            return

        # Discovery and health probes are intentionally independent of host auth.
        if any(path.startswith(prefix) for prefix in _UNGATED_PREFIXES):
            await self._app(scope, receive, send)
            return

        # Preflight carries no application credentials; the subsequent request is gated.
        if scope["type"] == "http" and scope.get("method", "").upper() == "OPTIONS":
            await self._app(scope, receive, send)
            return

        # Trust only the ASGI peer address for loopback; forwarded headers are spoofable
        # unless a reverse proxy and its trust boundary are configured separately.
        if cfg.loopback_bypass and _is_loopback_client(scope):
            await self._app(scope, receive, send)
            return

        # HTTP and WebSocket handshakes carry host credentials in different shapes.
        if scope["type"] == "http":
            supplied = _bearer_from_http_headers(scope)
        else:
            # Prefer WebSocket subprotocol credentials over the query fallback.
            supplied = _token_from_ws_handshake(scope)

        if not supplied or not _constant_time_match(supplied, cfg.host_token):
            await _reject(scope, send)
            return

        await self._app(scope, receive, send)


# Credential extraction and rejection helpers.


def _resolve_config(scope: Scope) -> AuthConfig:
    """Return the app policy snapshot or load one when state is unavailable."""
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
    """Extract the host token without colliding with user bearer tokens.

    ``X-KT-Host-Token`` takes precedence and permits a separate user token in
    ``Authorization``. Bearer fallback preserves clients that only use host auth.
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
    # The dedicated header is unambiguous when host and user auth are both enabled.
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
    """Extract a WebSocket token from subprotocol metadata or the query string.

    Subprotocol transport avoids access-log exposure; the query form supports clients
    that cannot set WebSocket subprotocols.
    """
    # The header may advertise multiple comma-separated subprotocols.
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
    # Query transport is a compatibility fallback for limited clients.
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
            # Decode percent-escaped token characters without parsing unrelated fields.
            try:
                return unquote(value).strip()
            except Exception:  # pragma: no cover - defensive
                return value.strip()
    return ""


def _constant_time_match(supplied: str, expected: str) -> bool:
    """Compare UTF-8 token encodings without content-dependent timing."""
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
            # Standards-aware clients can use this challenge to request credentials.
            (b"www-authenticate", b'Bearer realm="kohakuterrarium"'),
        ]
        await send({"type": "http.response.start", "status": 401, "headers": headers})
        await send({"type": "http.response.body", "body": body})
    else:
        # Private code 4401 communicates authentication failure on the WebSocket surface.
        await send(
            {"type": "websocket.close", "code": 4401, "reason": "host token required"}
        )


# Keep ASGI typing aliases local to the auth adapter surface.
_ASGIApp = ASGIApp
_Receive = Receive
_Send = Send
_Awaitable = Awaitable
_Callable = Callable
_Any = Any


__all__ = ["HostTokenMiddleware"]
