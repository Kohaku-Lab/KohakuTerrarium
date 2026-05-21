"""FastAPI dependencies.

Exposes the active :class:`TerrariumService` as a process-level
singleton.  Three boot paths set the singleton:

- ``standalone`` mode (default): a fresh :class:`Terrarium` +
  :class:`LocalTerrariumService` is constructed lazily on first
  :func:`get_service` call.
- ``lab-host`` mode: ``cli/serve.py`` builds a
  :class:`MultiNodeTerrariumService` at boot and calls
  :func:`set_service`.
- Tests: call :func:`set_service` with a custom instance, then reset
  with :func:`set_service(None)` (or rebuild between cases).

``get_engine`` remains for back-compat with single-host routes.  In
lab-host mode the host runs **no agent engine** — ``get_engine``
returns the host's *coordination* engine (an always-empty Terrarium
that only holds cross-node channel objects) and emits a one-time
warning per route.  A route that gets results back from that engine
in lab-host mode is looking at nothing; it must migrate to
``Depends(get_service)`` for real cross-node visibility.
"""

import os
import sys
from pathlib import Path

from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection

from kohakuterrarium.api.auth.dependencies import get_auth_config, get_optional_user
from kohakuterrarium.api.auth.engine_pool import EnginePool
from kohakuterrarium.api.auth.models import User
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
# Track which call sites have already heard about get_engine being
# multi-node-blind so we don't spam every request with the same warning.
_engine_legacy_warned: set[str] = set()


def _session_dir() -> str:
    """Resolve the session dir fresh, honouring KT_SESSION_DIR / KT_CONFIG_DIR.

    Previously a module constant computed at import time which ignored
    both env vars for tests that set them after import.  Now resolves
    on every call: explicit ``KT_SESSION_DIR`` wins, else falls back to
    ``config_dir() / "sessions"`` which honours ``KT_CONFIG_DIR``.
    """
    explicit = os.environ.get("KT_SESSION_DIR")
    if explicit:
        return explicit
    return str(config_dir() / "sessions")


# Back-compat — display only; live reads use ``_session_dir()``.
_DEFAULT_SESSION_DIR = str(Path.home() / ".kohakuterrarium" / "sessions")


def set_service(service: TerrariumService | None) -> None:
    """Install (or clear) the process-wide active service.

    Called once at boot by :mod:`cli/serve.py`.  Tests can call this to
    inject a custom service and pass ``None`` to reset between cases.
    """
    global _service, _engine_legacy
    _service = service
    # Clear the legacy engine reference; next get_engine() will pull
    # afresh from the new service if applicable.
    _engine_legacy = None


def get_service(
    conn_info: HTTPConnection,
    user: User | None = Depends(get_optional_user),
) -> TerrariumService:
    """Return the active :class:`TerrariumService` for a request.

    Takes :class:`HTTPConnection` so the same dep works on both HTTP
    routes (``Request``) and WebSocket routes (``WebSocket``).  WS
    routes that depend on this get the same per-user routing + L4
    enforcement as their HTTP counterparts.

    Routing rules:

    1. **Multi-node mode** (``app.state.service`` is a non-Local
       service like :class:`MultiNodeTerrariumService`): the singleton
       is returned as-is.  The multi-node layer does its own
       per-creature routing across worker nodes; per-user routing on
       top is a 2.0+ topic.
    2. **Single-host + L4 enabled**: each authenticated user gets a
       per-user :class:`Terrarium` from the engine pool, wrapped in a
       fresh :class:`LocalTerrariumService`.  Routing-time rules:
       - ``multi_user="required"``: anonymous request → 401.  Routes
         that don't strictly need a user (capabilities probe, login)
         opt out by calling :func:`get_service_legacy` directly or
         carrying their own ``Depends(get_optional_user)``.
       - ``multi_user="optional"``: anonymous → shared engine
         (``None`` slot); authenticated → per-user.
    3. **Single-host + L4 disabled**: legacy behaviour — one global
       service.

    This is the request-scoped FastAPI dependency.  Non-HTTP callers
    (CLI / tests / lab) should use :func:`get_service_legacy` which
    skips the per-request branches.
    """
    global _service
    # Multi-node mode: respect the singleton.  Detected by the absence
    # of an ``engine`` attribute on the service (only LocalTerrariumService
    # carries one).
    if _service is not None and not isinstance(_service, LocalTerrariumService):
        return _service

    pool: EnginePool | None = getattr(conn_info.app.state, "engine_pool", None)
    auth_config = get_auth_config(conn_info)
    if pool is not None and auth_config.multi_user_enabled:
        # ``required`` mode: every engine-handing route MUST resolve
        # to a real user.  An anonymous fall-through to the shared
        # ``None`` slot was the audit-caught bug — it let an
        # L2-authenticated caller skate past L4 by simply not sending
        # a session cookie.  The 401 here propagates as a normal
        # FastAPI exception; the frontend's connection state machine
        # treats it like any other ``user``-flavoured auth challenge.
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
        service.set_runtime_graph_meta_lookup(get_session_meta)
        return service

    # Legacy single-engine path — standalone mode without L4.
    if _service is None:
        engine = Terrarium(session_dir=_session_dir())
        _service = LocalTerrariumService(engine)
        _service.set_runtime_graph_meta_lookup(get_session_meta)
    return _service


def get_service_legacy() -> TerrariumService:
    """Non-request variant — CLI / lab / test code paths.

    Always returns the legacy single global service.  Has no
    awareness of per-user routing because there's no request /
    user identity to scope from.
    """
    global _service
    if _service is None:
        engine = Terrarium(session_dir=_session_dir())
        _service = LocalTerrariumService(engine)
        _service.set_runtime_graph_meta_lookup(get_session_meta)
    return _service


def get_engine() -> Terrarium:
    """Return a singleton :class:`Terrarium` engine.

    Back-compat shim for routes not yet migrated to
    :func:`get_service`.

    - **standalone**: the host-local agent engine.
    - **lab-host**: the host's *coordination* engine — an always-empty
      Terrarium that holds only cross-node channel objects.  The host
      runs no agents, so a route reading agent state off this engine
      sees nothing; a one-time warning per call site flags the missing
      migration.  Cross-node visibility requires :func:`get_service`.
    """
    global _engine_legacy
    svc = get_service_legacy()
    if isinstance(svc, LocalTerrariumService):
        _engine_legacy = svc.engine
    else:
        # Multi-node (lab-host): no host agent engine.  Fall back to the
        # coordination engine so a ``Depends(get_engine)`` route resolves
        # instead of 500-ing — but it is provably agent-free.
        _engine_legacy = getattr(svc, "coordination_engine", None)
        if _engine_legacy is None:
            raise RuntimeError(
                "get_engine() called in lab-host mode with no coordination "
                "engine; route must migrate to Depends(get_service)"
            )
        # Emit a once-per-call-site warning so the missing migration
        # surfaces in the daemon log without spamming every request.
        # Caller filename:lineno keys the dedup set.
        frame = sys._getframe(1)
        callsite = f"{frame.f_code.co_filename}:{frame.f_lineno}"
        if callsite not in _engine_legacy_warned:
            _engine_legacy_warned.add(callsite)
            logger.warning(
                "get_engine() in lab-host mode returns the (agent-free) "
                "coordination engine — route needs Depends(get_service)",
                extra={"callsite": callsite},
            )
    # ``_runtime_prompt.attach`` is the per-engine convention for
    # wiring the runtime-graph prompt; production Terrarium always
    # exposes it.  The ``getattr`` is defensive against a future
    # coordination-engine type that doesn't carry the prompt
    # internals — the audit flagged the unconditional attribute
    # access as a hidden coupling.  A no-op attach when missing
    # keeps ``get_engine`` honest about returning *some* engine
    # instead of crashing on a fresh type.
    runtime_prompt = getattr(_engine_legacy, "_runtime_prompt", None)
    if runtime_prompt is not None:
        runtime_prompt.attach()
    return _engine_legacy
