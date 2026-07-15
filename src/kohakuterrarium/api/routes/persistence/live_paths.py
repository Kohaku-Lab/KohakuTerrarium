"""Resolve a LIVE session to its engine-attached, already-open store.

Viewer and history routes may address a live session by graph ID or by its
file stem. Autosession files are named by creature ID, so ordinary on-disk name
resolution cannot find the graph-ID form. These helpers locate the engine-owned
``SessionStore`` and let read-only routes reuse it instead of opening a second
connection to an actively written SQLite file, which can raise ``SQLITE_IOERR``
on POSIX.

Lab hosts have no local engine; lookups return ``None`` so callers can use their
on-disk fallback.
"""

from pathlib import Path

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio._runtime import host_engine_or_none


def live_store_entry(service, session_name: str) -> tuple[str, SessionStore] | None:
    """Return the open ``(graph_id, store)`` matching a live session name.

    Graph-ID lookup takes precedence. File-stem matching supports saved-listing
    names for sessions that are still running, and closed stores are excluded.
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
    """Return the live store matching ``session_name``, if one is attached."""
    entry = live_store_entry(service, session_name)
    return entry[1] if entry is not None else None


def live_store_path(service, session_name: str) -> Path | None:
    """Return the attached live store's on-disk path when available."""
    store = live_store_for(service, session_name)
    if store is None:
        return None
    path = getattr(store, "_path", None)
    return Path(path) if path else None
