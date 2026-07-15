"""Provide FastAPI dependencies for auth policy, admin gates, and users.

Application state supplies a coherent auth snapshot when available. Direct router
mounts fall back to loading configuration. User resolution supports both HTTP and
WebSocket connections, preferring browser sessions over bearer API tokens.
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

# Login and dependency code must use the same cookie name.
SESSION_COOKIE_NAME = "kt_session"


def get_auth_config(conn_info: HTTPConnection) -> AuthConfig:
    """Return the app snapshot or load policy when no app snapshot exists.

    ``HTTPConnection`` keeps the dependency valid for both HTTP and WebSocket routes.
    """
    cached = getattr(conn_info.app.state, "auth_config", None)
    if isinstance(cached, AuthConfig):
        return cached
    return load_auth_config()


def verify_admin_token(
    request: Request,
    x_admin_token: str = Header(default=""),
) -> None:
    """Require the independent admin secret for configuration mutations.

    An empty configured secret disables this gate; enabled comparisons use constant
    time and return a distinct challenge from user authentication.
    """
    cfg = get_auth_config(request)
    if not cfg.admin_token_enabled:
        return  # An empty configured token explicitly disables this gate.
    if not x_admin_token or not _constant_time_match(x_admin_token, cfg.admin_token):
        raise HTTPException(
            status_code=401,
            detail={
                "error": "admin_required",
                "message": "admin token required for this operation",
            },
            headers={
                # Distinguish administrative authorization from a user-login challenge.
                "X-Auth-Required": "admin",
            },
        )


def _constant_time_match(supplied: str, expected: str) -> bool:
    """Compare UTF-8 secrets with the same constant-time semantics as middleware."""
    try:
        return secrets.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        )
    except (AttributeError, UnicodeEncodeError):  # pragma: no cover - defensive
        return False


def _bearer_token_from_header(authorization: str | None) -> str:
    """Return a stripped bearer token, or an empty string for malformed headers."""
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
    """Resolve an optional user from a session cookie, then a bearer token.

    Disabled user auth performs no database lookup. Cookie access goes through the
    connection object so routes may independently use a ``session_id`` path parameter
    without FastAPI parameter-name collisions.
    """
    cfg = get_auth_config(conn_info)
    if not cfg.multi_user_enabled:
        return None  # Disabled user auth has no user identity to resolve.

    session_id = conn_info.cookies.get(SESSION_COOKIE_NAME, "")

    with connection() as conn:
        # Session validation enforces both absolute and configured idle expiry.
        if session_id:
            user = get_session_user(
                conn,
                session_id,
                idle_minutes=cfg.session_idle_minutes,
            )
            if user is not None:
                touch_last_seen(conn, session_id)
                return user
        # Bearer tokens are the fallback when no valid browser session exists.
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
    """Require a resolved user and return a user-specific authentication challenge."""
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
        # User-specific endpoints remain unavailable when accounts are disabled.
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
