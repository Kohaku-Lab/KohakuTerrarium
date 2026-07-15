"""Keep saved-session index sidecars synchronized with live stores.

Each live SessionStore receives a debounced :class:`SessionIndexHook`, allowing
activity, preview, and status fields to update without explicit reconciliation.
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


# Session stop receives this registry to flush and detach each live hook.
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
                logger.warning(
                    "prior index-hook detach failed", error=str(exc), exc_info=True
                )
        index = get_session_index_default(Path(sess_dir))
        _session_index_hooks[sid] = SessionIndexHook(store, index)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "session-index hook attach failed", error=str(exc), exc_info=True
        )


def registry() -> dict[str, Any]:
    """The dict ``stop_session`` receives so it can flush+detach."""
    return _session_index_hooks
