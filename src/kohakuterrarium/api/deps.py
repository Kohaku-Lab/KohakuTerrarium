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


def get_service() -> TerrariumService:
    """Return the active :class:`TerrariumService`.

    Lazy default in standalone mode: constructs a fresh
    :class:`Terrarium` + :class:`LocalTerrariumService` on first call.
    Routes use ``Depends(get_service)`` to receive this.
    """
    global _service
    if _service is None:
        engine = Terrarium(session_dir=_session_dir())
        _service = LocalTerrariumService(engine)
        # Standalone mode: same meta-lookup wiring as lab-host so the
        # ``runtime_graph_snapshot`` enrichment behaves identically.
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
    svc = get_service()
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
    _engine_legacy._runtime_prompt.attach()
    return _engine_legacy
