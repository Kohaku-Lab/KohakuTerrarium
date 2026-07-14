"""Resolve a LIVE session to its engine-attached, already-open store.

The viewer / history frontend addresses a live session by its graph_id
(``graph_<uuid>``) or by its on-disk file stem, but the autosession file
is named by creature_id, so plain on-disk name resolution
(``resolve_session_path_default``) misses the graph_id form. The engine
holds the live ``SessionStore`` keyed by graph_id; these helpers return
that store so read-only viewer / history routes can REUSE its open
handles instead of opening the same SQLite file a second time — a
second open of an actively-written store is unreliable on POSIX
(``SQLITE_IOERR`` on the tables the live writer touched).

Multi-node / lab-host has no host-local engine, so every lookup returns
``None`` and callers fall through to on-disk resolution (never crash).
"""

from pathlib import Path

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio._runtime import host_engine_or_none


def live_store_entry(service, session_name: str) -> tuple[str, SessionStore] | None:
    """Return ``(graph_id, store)`` for the live store behind ``session_name``.

    Matches the engine's graph_id key first, then any attached store whose
    on-disk file stem equals ``session_name`` (the saved-listing name of a
    still-running session). Closed stores never match.
    """
    engine = host_engine_or_none(service)
    if engine is None:
        return None
    stores = getattr(engine, "_session_stores", {}) or {}
    store = stores.get(session_name)
    if store is not None and not getattr(store, "_closed", False):
        return session_name, store
    for graph_id, candidate in stores.items():
        if getattr(candidate, "_closed", False):
            continue
        path = getattr(candidate, "_path", None)
        if path and Path(path).stem == session_name:
            return graph_id, candidate
    return None


def live_store_for(service, session_name: str) -> SessionStore | None:
    """The live store attached under ``session_name``, or ``None``."""
    entry = live_store_entry(service, session_name)
    return entry[1] if entry is not None else None


def live_store_path(service, session_name: str) -> Path | None:
    """The on-disk file of the live store, or ``None`` when not live."""
    store = live_store_for(service, session_name)
    if store is None:
        return None
    path = getattr(store, "_path", None)
    return Path(path) if path else None
