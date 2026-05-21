"""FastAPI dependencies for the auth layer.

| Dep | Layer | Purpose |
|---|---|---|
| :func:`get_auth_config` | (infra) | Snapshot of ``[auth]`` config |
| :func:`verify_admin_token` | L3 | Gate config-mutation routes via ``X-Admin-Token`` |
| :func:`get_current_user` | L4 | Resolve User from cookie OR Bearer (Phase E) |
| :func:`get_optional_user` | L4 | Same, but anonymous OK (Phase E) |

The config dependency reads from ``app.state.auth_config`` when the
FastAPI app sets it at boot (the production path), otherwise falls
back to a fresh :func:`load_auth_config` call so unit tests that drive
the router via :class:`fastapi.testclient.TestClient` without going
through :func:`create_app` still get a sane config.
"""

import secrets

from fastapi import Header, HTTPException, Request
from starlette.requests import HTTPConnection

from kohakuterrarium.api.auth.config import AuthConfig, load_auth_config
from kohakuterrarium.api.auth.db import connection
from kohakuterrarium.api.auth.models import User
from kohakuterrarium.api.auth.sessions import (
    get_session_user,
    touch_last_seen,
)
from kohakuterrarium.api.auth.tokens import get_token_user

# Cookie name reused everywhere — defined once so the login route and
# the dependency can't drift.
SESSION_COOKIE_NAME = "kt_session"


def get_auth_config(conn_info: HTTPConnection) -> AuthConfig:
    """Return the active :class:`AuthConfig`.

    Reads ``conn_info.app.state.auth_config`` when present (the
    production path — :func:`create_app` snapshots once at boot).
    Falls back to a fresh load when state is absent so unit tests
    mounting the router directly still work.

    Takes :class:`HTTPConnection` rather than :class:`Request` so the
    same dependency works on WS routes (WebSocket subclasses
    HTTPConnection).
    """
    cached = getattr(conn_info.app.state, "auth_config", None)
    if isinstance(cached, AuthConfig):
        return cached
    return load_auth_config()


def verify_admin_token(
    request: Request,
    x_admin_token: str = Header(default=""),
) -> None:
    """L3 — gate config-mutating routes via ``X-Admin-Token`` header.

    No-op when ``auth.admin_token`` is empty (off).  Otherwise requires
    the header to constant-time-equal the configured token.  Raises
    HTTPException 401 with a structured detail on miss.

    Mirrors KohakuHub's ``verify_admin_token`` pattern: independent of
    user auth, gated by a separate config secret, intended for
    "anyone with the host token can use; only admin can change config."
    """
    cfg = get_auth_config(request)
    if not cfg.admin_token_enabled:
        return  # gate off, no-op
    if not x_admin_token or not _constant_time_match(x_admin_token, cfg.admin_token):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "admin_required",
                "message": "admin token required for this operation",
            },
            headers={
                # Signal to the frontend that this 401 is L3-specific —
                # the connection state machine raises the admin-password
                # modal rather than re-prompting login.
                "X-Auth-Required": "admin",
            },
        )


def _constant_time_match(supplied: str, expected: str) -> bool:
    """Constant-time UTF-8 compare for short secrets.

    Mirrors the shape used in :mod:`api.auth.middleware` so both layers'
    secret-compare semantics stay aligned.
    """
    try:
        return secrets.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        )
    except (AttributeError, UnicodeEncodeError):  # pragma: no cover - defensive
        return False


def _bearer_token_from_header(authorization: str | None) -> str:
    """Extract the token portion of an ``Authorization: Bearer <x>``
    header.  Empty string on miss / wrong scheme."""
    if not authorization:
        return ""
    parts = authorization.split(None, 1)
    if len(parts) != 2:
        return ""
    scheme, token = parts
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def get_optional_user(
    conn_info: HTTPConnection,
    authorization: str | None = Header(default=None),
) -> User | None:
    """Resolve the active user from cookie or Bearer; ``None`` when
    anonymous and ``multi_user`` is off/optional.

    When ``multi_user`` is ``"required"``, an anonymous request gets
    ``None`` here — :func:`get_current_user` is the strict variant
    that raises 401 instead.

    Cookie wins over Bearer (web UI path is primary); Bearer is the
    fallback for CLI / bundled apps / cross-origin web frontends.

    The parameter is typed as :class:`HTTPConnection` — the common
    base of :class:`Request` (HTTP routes) and :class:`WebSocket` (WS
    routes) — so this single dependency works on both surfaces.
    Both expose ``.app``, ``.cookies``, and ``.headers``, which is
    everything the resolver needs.

    Reads the session cookie via ``conn_info.cookies`` (not via a
    ``Cookie(...)`` parameter) so the dependency can be installed on
    routes that take ``session_id`` as a PATH param without FastAPI
    treating them as the same identifier.
    """
    cfg = get_auth_config(conn_info)
    if not cfg.multi_user_enabled:
        return None  # L4 off — no user resolution at all

    session_id = conn_info.cookies.get(SESSION_COOKIE_NAME, "")

    with connection() as conn:
        # 1. Cookie path.  ``idle_minutes`` honours the configured
        # ``session_idle_minutes`` so the operator can require
        # re-login after a quiet window (audit caught this knob
        # being defined but never consulted).
        if session_id:
            user = get_session_user(
                conn,
                session_id,
                idle_minutes=cfg.session_idle_minutes,
            )
            if user is not None:
                touch_last_seen(conn, session_id)
                return user
        # 2. Bearer-token path.
        bearer = _bearer_token_from_header(authorization)
        if bearer:
            user = get_token_user(conn, bearer)
            if user is not None:
                return user
    return None


def get_current_user(
    conn_info: HTTPConnection,
    authorization: str | None = Header(default=None),
) -> User:
    """Strict variant of :func:`get_optional_user`.

    Raises ``HTTPException(401)`` when no valid auth is presented.
    The 401 detail carries ``error: "auth_required"`` and the
    ``X-Auth-Required: user`` response header so the frontend can
    distinguish this from L3's admin gate.
    """
    user = get_optional_user(conn_info, authorization)
    if user is None:
        cfg = get_auth_config(conn_info)
        if cfg.multi_user_enabled:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "auth_required",
                    "message": "user authentication required",
                },
                headers={"X-Auth-Required": "user"},
            )
        # L4 off — endpoints that require a real user (e.g. /auth/me)
        # behave as 401 because there's no user concept to honour.
        raise HTTPException(
            status_code=401,
            detail={
                "error": "multi_user_disabled",
                "message": "user accounts are not enabled on this host",
            },
        )
    return user


__all__ = [
    "SESSION_COOKIE_NAME",
    "get_auth_config",
    "get_current_user",
    "get_optional_user",
    "verify_admin_token",
]
