"""Keep the live-session list in step with a hosting engine.

:func:`track` subscribes to the engine's session stores (new sessions, merge
and split children) and records each as live; :func:`untrack_store` is the
user stop / end path and :func:`forget` the delete path. Server shutdown and
crashes never touch the list, so boot finds what was running.
"""

from pathlib import Path
from typing import Any

import kohakuterrarium.session.run_state as run_state
from kohakuterrarium.studio.sessions.live.registry import live_sessions
from kohakuterrarium.terrarium.store_observers import observe_session_stores
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_LISTENER_ATTR = "_kt_live_listener"


def _store_path(store: Any) -> str | None:
    if store is None or getattr(store, "_readonly", False):
        return None
    path = getattr(store, "path", None)
    return str(path) if path else None


def mark_live(engine: Any, graph_id: str, store: Any) -> dict | None:
    """Record ``store`` as a live session of ``engine`` (requires blocking)."""
    path = _store_path(store)
    if path is None:
        return None
    run_state.thaw(store)
    try:
        run_state.set_lifecycle(store, live=True)
    except Exception as exc:  # noqa: BLE001 - a closed store still gets its row
        logger.warning("lifecycle live mark failed", path=path, error=str(exc))
    return live_sessions().add(
        path,
        session_id=graph_id,
        session_dir=str(getattr(engine, "_session_dir", None) or Path(path).parent),
        boot_id=run_state.BOOT_ID,
        server=run_state.SERVER_KEY,
    )


def track(engine: Any) -> None:
    """Record every current and future session store of ``engine`` as live."""
    if engine is None:
        return
    listener = getattr(engine, _LISTENER_ATTR, None)
    if listener is None:

        def listener(graph_id: str, store: Any) -> None:
            mark_live(engine, graph_id, store)

        setattr(engine, _LISTENER_ATTR, listener)
    stores = observe_session_stores(engine, listener)
    for graph_id, store in list(stores.items()):
        mark_live(engine, graph_id, store)


def untrack_store(store: Any, *, reason: str = run_state.STOP_USER) -> None:
    """A user stopped or ended the session: drop its row and record why."""
    path = _store_path(store)
    if path is None:
        return
    live_sessions().remove(path)
    try:
        run_state.set_lifecycle(store, live=False, stop_reason=reason)
    except Exception as exc:  # noqa: BLE001
        logger.warning("lifecycle stop mark failed", path=path, error=str(exc))


def forget(path: str | Path) -> bool:
    """A session file was deleted: drop its row."""
    return live_sessions().remove(path)
