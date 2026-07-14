"""Resolve a LIVE session (in-memory graph_id) to its on-disk store file.

The viewer / history frontend addresses a live session by its graph_id
(``graph_<uuid>``), but the autosession file is named by creature_id, so
plain on-disk name resolution (``resolve_session_path_default``) misses
it and the viewer tabs 404. The engine holds the live ``SessionStore``
keyed by graph_id; this returns its file so the read-only viewer /
history routes can open the same SQLite DB — committed rows are visible
through WAL while the writer-locked live store keeps writing.

Multi-node / lab-host has no host-local engine, so the lookup returns
``None`` and callers fall through to on-disk resolution (never crash).
"""

from pathlib import Path

from kohakuterrarium.studio._runtime import host_engine_or_none


def live_store_path(service, session_name: str) -> Path | None:
    """Return the file of the live store attached under ``session_name``.

    ``session_name`` is the graph_id the frontend sends for a live
    session. Returns ``None`` when there is no host-local engine (lab
    host) or no live store for that id (a genuinely saved session).
    """
    engine = host_engine_or_none(service)
    if engine is None:
        return None
    store = getattr(engine, "_session_stores", {}).get(session_name)
    if store is None:
        return None
    path = getattr(store, "_path", None)
    return Path(path) if path else None
