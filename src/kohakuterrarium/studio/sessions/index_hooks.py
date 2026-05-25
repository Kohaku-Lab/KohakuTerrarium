"""SessionIndexHook lifecycle wiring.

Attaches a debounced :class:`SessionIndexHook` to every live
``SessionStore`` so updates flow into the session-index sidecar as
events arrive — without this the sidecar would only refresh via the
process-startup reconcile / explicit ``?refresh=true``, leaving
in-flight sessions with stale ``last_active`` / ``preview`` / ``status``
fields.

Lives in its own module so :mod:`studio.sessions.lifecycle` stays
under the 1000-line hard cap.  The registry below is module-private;
``lifecycle`` reaches it via the public functions in this module.
"""

from pathlib import Path
from typing import Any

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index import (
    SessionIndexHook,
    get_session_index_default,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Per-session live SessionIndexHook keyed by ``session_id``.
# Detached on session stop (see ``studio.sessions.stop.stop_session``
# which receives this dict via the ``index_hooks`` kwarg).
_session_index_hooks: dict[str, SessionIndexHook] = {}


def attach(sid: str, store: SessionStore, sess_dir: str | Path) -> None:
    """Bind a debounced index-update hook to ``store``.

    Best-effort: a failure here doesn't break the session — the
    startup reconcile + ``?refresh=true`` paths still keep the
    sidecar honest, just with a delay.  Idempotent on ``sid`` —
    a re-attach (e.g. cluster member adoption) detaches the prior
    hook first.
    """
    try:
        existing = _session_index_hooks.pop(sid, None)
        if existing is not None:
            try:
                existing.detach()
            except Exception as exc:  # noqa: BLE001
                logger.debug("prior index-hook detach failed", error=str(exc))
        index = get_session_index_default(Path(sess_dir))
        _session_index_hooks[sid] = SessionIndexHook(store, index)
    except Exception as exc:  # noqa: BLE001
        logger.debug("session-index hook attach failed", error=str(exc))


def registry() -> dict[str, Any]:
    """The dict ``stop_session`` receives so it can flush+detach."""
    return _session_index_hooks
