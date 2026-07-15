"""Expose the FastAPI server's authentication and authorization surface.

Authentication remains an API adapter concern rather than part of the core agent
framework. This module centralizes the router, configuration, user dependencies,
and public user model consumed by sibling API modules.
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
