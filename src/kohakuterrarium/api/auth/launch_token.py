"""Gate a desktop-owned server behind a per-launch token and a session cookie.

The desktop shell starts the server with ``KT_DESKTOP_LAUNCH_TOKEN`` set and opens
``/?kt_launch=<token>``. The first request that presents the token is redirected to
the same URL without it and receives an ``HttpOnly; SameSite=Strict`` cookie; every
other HTTP request and WebSocket handshake must carry that cookie. Requests whose
``Host`` or ``Origin`` is not loopback are refused, which closes DNS-rebinding and
cross-site access to the loopback port. With no token configured the middleware is
not installed.
"""

import os
import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, urlencode, urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

from kohakuterrarium.utils.desktop_launch import COOKIE_NAME, LAUNCH_ENV, QUERY_PARAM

_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
_UNGATED_PATHS = frozenset({"/healthz", "/readyz"})


class LaunchGate:
    """Hold the one-time launch token and the session secret it is exchanged for."""

    def __init__(self, token: str):
        self._token = token
        self._session = secrets.token_urlsafe(32)
        self._exchanged = False

    @classmethod
    def from_env(cls) -> "LaunchGate | None":
        """Read and remove the launch token from the environment."""
        token = os.environ.pop(LAUNCH_ENV, "").strip()
        return cls(token) if token else None

    def exchange(self, supplied: str) -> str | None:
        """Return the session secret for a correct, unused launch token."""
        if self._exchanged or not secrets.compare_digest(
            supplied.encode("utf-8"), self._token.encode("utf-8")
        ):
            return None
        self._exchanged = True
        return self._session

    def accepts_cookie(self, value: str) -> bool:
        return secrets.compare_digest(
            value.encode("utf-8"), self._session.encode("utf-8")
        )


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []) or []:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def _hostname(netloc: str) -> str:
    if netloc.startswith("["):
        return netloc.split("]", 1)[0] + "]"
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc


def _cookie(scope: Scope) -> str:
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(_header(scope, b"cookie"))
    except ValueError:
        return ""
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else ""


class LaunchTokenMiddleware:
    """Enforce :class:`LaunchGate` on HTTP requests and WebSocket handshakes."""

    def __init__(self, app: ASGIApp, gate: LaunchGate):
        self._app = app
        self._gate = gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        if not self._origin_allowed(scope):
            await self._refuse(scope, send, 403, "foreign origin")
            return
        if scope["type"] == "http" and scope.get("path") in _UNGATED_PATHS:
            await self._app(scope, receive, send)
            return
        if self._gate.accepts_cookie(_cookie(scope)):
            await self._app(scope, receive, send)
            return
        if scope["type"] == "http" and scope.get("method") == "GET":
            session = self._exchange(scope)
            if session is not None:
                await self._redirect(scope, send, session)
                return
        await self._refuse(scope, send, 401, "launch token required")

    def _origin_allowed(self, scope: Scope) -> bool:
        host = _header(scope, b"host")
        if _hostname(host) not in _LOOPBACK_NAMES:
            return False
        origin = _header(scope, b"origin")
        return not origin or urlsplit(origin).netloc == host

    def _exchange(self, scope: Scope) -> str | None:
        query = scope.get("query_string", b"").decode("latin-1")
        for key, value in parse_qsl(query):
            if key == QUERY_PARAM:
                return self._gate.exchange(value)
        return None

    async def _redirect(self, scope: Scope, send: Send, session: str) -> None:
        query = [
            (key, value)
            for key, value in parse_qsl(
                scope.get("query_string", b"").decode("latin-1")
            )
            if key != QUERY_PARAM
        ]
        location = scope.get("path", "/") + (f"?{urlencode(query)}" if query else "")
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [
                    (b"location", location.encode("latin-1")),
                    (
                        b"set-cookie",
                        f"{COOKIE_NAME}={session}; Path=/; HttpOnly; SameSite=Strict".encode(
                            "latin-1"
                        ),
                    ),
                    (b"content-length", b"0"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def _refuse(self, scope: Scope, send: Send, status: int, reason: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401, "reason": reason})
            return
        body = reason.encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = [
    "COOKIE_NAME",
    "LAUNCH_ENV",
    "LaunchGate",
    "LaunchTokenMiddleware",
    "QUERY_PARAM",
]
