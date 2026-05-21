"""Authentication / authorization for the FastAPI server.

Public surface — what other modules under :mod:`kohakuterrarium.api`
import.  Nothing outside ``src/kohakuterrarium/api/`` imports from
here; the auth concern is scoped to the API server, not the
framework.

Phase A exports: ``router`` (carrying ``/capabilities``),
``AuthConfig`` + ``load_auth_config`` (config snapshot for boot
plumbing), and ``get_auth_config`` (FastAPI dependency).
"""

from kohakuterrarium.api.auth.config import AuthConfig, load_auth_config
from kohakuterrarium.api.auth.dependencies import (
    SESSION_COOKIE_NAME,
    get_auth_config,
    get_current_user,
    get_optional_user,
    verify_admin_token,
)
from kohakuterrarium.api.auth.models import User
from kohakuterrarium.api.auth.routes import router

__all__ = [
    "AuthConfig",
    "SESSION_COOKIE_NAME",
    "User",
    "get_auth_config",
    "get_current_user",
    "get_optional_user",
    "load_auth_config",
    "router",
    "verify_admin_token",
]
