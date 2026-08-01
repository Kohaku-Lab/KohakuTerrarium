"""Resolve request-scoped and process-wide Terrarium services.

Standalone mode lazily owns one local service, Laboratory host mode installs a
multi-node service, and user-authenticated requests may resolve isolated local
engines. ``get_engine`` exposes only the local or coordination engine and therefore
cannot provide cross-node creature visibility.
"""

import os
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path

from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection

from kohakuterrarium.api.auth.dependencies import get_auth_config, get_optional_user
from kohakuterrarium.api.auth.engine_pool import EnginePool, _user_session_dir
from kohakuterrarium.api.auth.models import User
from kohakuterrarium.studio.identity import drive_settings as _drive_settings
from kohakuterrarium.studio.sessions.lifecycle import get_session_meta
from kohakuterrarium.terrarium import (
    LocalTerrariumService,
    Terrarium,
    TerrariumService,
)
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_service: TerrariumService | None = None
_engine_legacy: Terrarium | None = None
# Deduplicate warnings for call sites that request an engine without cross-node visibility.
_engine_legacy_warned: set[str] = set()


def _session_dir() -> str:
    """Resolve the live session directory with ``KT_SESSION_DIR`` precedence.

    Resolving on each call also allows ``KT_CONFIG_DIR`` changes to affect the
    fallback path rather than freezing environment state at import time.
    """
    explicit = os.environ.get("KT_SESSION_DIR")
    if explicit:
        return explicit
    return str(config_dir() / "sessions")


# Retained for display compatibility; operational reads must use ``_session_dir()``.
_DEFAULT_SESSION_DIR = str(Path.home() / ".kohakuterrarium" / "sessions")


def _host_drive_kwargs() -> dict:
    """Resolve one immutable host Drive configuration for a new engine."""
    return _drive_settings.resolve_drive_kwargs()


def set_service(service: TerrariumService | None) -> None:
    """Install or clear the process-wide service and invalidate its engine view."""
    global _service, _engine_legacy
    _service = service
    # The cached engine belongs to the previous service and cannot survive replacement.
    _engine_legacy = None


def get_service(
    conn_info: HTTPConnection,
    user: User | None = Depends(get_optional_user),
) -> TerrariumService:
    """Return the service scoped to an HTTP or WebSocket connection.

    Multi-node services route requests themselves. On a single host, enabled user
    isolation selects an engine-pool slot by user identity; required mode rejects
    anonymous access instead of falling through to shared state. Without user
    isolation, all requests share the process-wide local service.
    """
    global _service
    # Multi-node services own their routing and must not be wrapped as local engines.
    if _service is not None and not isinstance(_service, LocalTerrariumService):
        return _service

    pool: EnginePool | None = getattr(conn_info.app.state, "engine_pool", None)
    auth_config = get_auth_config(conn_info)
    if pool is not None and auth_config.multi_user_enabled:
        # Required isolation must never map an anonymous request to the shared
        # pool slot, because that would bypass the user-authentication boundary.
        if user is None and auth_config.multi_user == "required":
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "auth_required",
                    "message": "user authentication required",
                },
                headers={"X-Auth-Required": "user"},
            )
        user_id = user.id if user is not None else None
        engine = pool.get_or_create(user_id)
        service = LocalTerrariumService(engine)
        # Session metadata lookup must bind to the same service instance as the engine.
        service.set_runtime_graph_meta_lookup(partial(get_session_meta, service))
        return service

    # Without user isolation, lazily construct the process-wide local service.
    if _service is None:
        engine = Terrarium(session_dir=_session_dir(), **_host_drive_kwargs())
        _service = LocalTerrariumService(engine)
        _service.set_runtime_graph_meta_lookup(partial(get_session_meta, _service))
    return _service


def get_service_factory(
    conn_info: HTTPConnection,
    user: User | None = Depends(get_optional_user),
) -> Callable[[], TerrariumService]:
    """Return a request-scoped lazy service resolver."""
    resolved: TerrariumService | None = None

    def resolve() -> TerrariumService:
        nonlocal resolved
        if resolved is None:
            resolved = get_service(conn_info, user)
        return resolved

    return resolve


def resolve_request_session_dir(
    conn_info: HTTPConnection,
    user: User | None = Depends(get_optional_user),
) -> Path:
    """Return the saved-session directory matching the request's engine namespace.

    Required user isolation rejects anonymous callers rather than exposing the
    shared session directory.
    """
    pool: EnginePool | None = getattr(conn_info.app.state, "engine_pool", None)
    auth_config = get_auth_config(conn_info)
    if pool is not None and auth_config.multi_user_enabled:
        if user is None and auth_config.multi_user == "required":
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "auth_required",
                    "message": "user authentication required",
                },
                headers={"X-Auth-Required": "user"},
            )
        user_id = user.id if user is not None else None
        return _user_session_dir(user_id)
    return Path(_session_dir())


def get_service_legacy() -> TerrariumService:
    """Return the process-wide service when no request identity is available."""
    global _service
    if _service is None:
        engine = Terrarium(session_dir=_session_dir(), **_host_drive_kwargs())
        _service = LocalTerrariumService(engine)
        _service.set_runtime_graph_meta_lookup(partial(get_session_meta, _service))
    return _service


def get_engine() -> Terrarium:
    """Return the local engine view of the process-wide service.

    In Laboratory host mode this is the agent-free coordination engine, so callers
    that need creature state must depend on :func:`get_service` instead.
    """
    global _engine_legacy
    svc = get_service_legacy()
    if isinstance(svc, LocalTerrariumService):
        _engine_legacy = svc.engine
    else:
        # Laboratory hosts expose the coordination engine for compatibility, but
        # it intentionally contains no local creatures.
        _engine_legacy = getattr(svc, "coordination_engine", None)
        if _engine_legacy is None:
            raise RuntimeError(
                "get_engine() called in lab-host mode with no coordination "
                "engine; route must migrate to Depends(get_service)"
            )
        # Filename and line identify the compatibility caller without logging per request.
        frame = sys._getframe(1)
        callsite = f"{frame.f_code.co_filename}:{frame.f_lineno}"
        if callsite not in _engine_legacy_warned:
            _engine_legacy_warned.add(callsite)
            logger.warning(
                "get_engine() in lab-host mode returns the (agent-free) "
                "coordination engine — route needs Depends(get_service)",
                extra={"callsite": callsite},
            )
    # Coordination-engine implementations are not required to expose prompt
    # internals, so prompt attachment remains capability-based.
    runtime_prompt = getattr(_engine_legacy, "_runtime_prompt", None)
    if runtime_prompt is not None:
        runtime_prompt.attach()
    return _engine_legacy
