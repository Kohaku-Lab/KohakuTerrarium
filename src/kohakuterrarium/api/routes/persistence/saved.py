"""Persistence saved — list / delete saved sessions.

Routes drain from the legacy ``api/routes/sessions.py``; all logic
lives in ``studio/persistence/store.py``. Mounted under both
``/api/persistence/saved`` and ``/api/sessions`` (URL preservation
for the existing frontend ``sessionAPI`` callers).
"""

import asyncio

from fastapi import APIRouter, HTTPException

from kohakuterrarium.studio.persistence.store import (
    build_session_index,
    delete_session_files,
    disk_usage,
    get_session_index,
    session_stats,
)

router = APIRouter()


@router.get("/disk-usage")
async def get_disk_usage():
    """Aggregate disk usage of the saved-session directory.

    Pure filesystem — stats every canonical session file + its
    SQLite sidecars without opening any database. Off-loaded to a
    worker thread so the directory walk doesn't block the event loop
    on large session collections.
    """
    return await asyncio.to_thread(disk_usage)


@router.get("/stats")
async def get_session_stats():
    """Aggregations over the cached session index.

    Cheap — reads the in-memory index built by ``get_session_index``
    (30s TTL). Does not force a rebuild. Run in a thread because a
    cold cache triggers the same blocking rebuild as ``list_sessions``.
    """
    return await asyncio.to_thread(session_stats)


@router.get("")
async def list_sessions(
    limit: int = 20,
    offset: int = 0,
    search: str = "",
    refresh: bool = False,
):
    """List saved sessions with search and pagination.

    Args:
        limit: Max sessions to return (default 20)
        offset: Skip first N sessions (for pagination)
        search: Filter by name, config, agents, preview (case-insensitive)
        refresh: Force rebuild the session index

    Index build opens every session SQLite to extract a preview, so
    we run the whole fetch+filter pipeline on a worker thread to keep
    other API calls responsive while the rail loads.
    """
    if refresh:
        await asyncio.to_thread(build_session_index)

    all_sessions = await asyncio.to_thread(get_session_index)

    # Server-side search
    if search:
        q = search.lower()

        def _as_str(v):
            """Defensive coerce — session metadata fields are usually strings
            but recent recordings may contain a list (e.g. multimodal
            preview blocks). Flatten anything to a single space-joined
            string for the search haystack.
            """
            if v is None:
                return ""
            if isinstance(v, str):
                return v
            if isinstance(v, list):
                return " ".join(_as_str(x) for x in v)
            if isinstance(v, dict):
                return " ".join(_as_str(x) for x in v.values())
            return str(v)

        all_sessions = [
            s
            for s in all_sessions
            if q
            in " ".join(
                _as_str(s.get(k, ""))
                for k in (
                    "name",
                    "config_path",
                    "config_type",
                    "terrarium_name",
                    "preview",
                    "pwd",
                    "agents",
                )
            ).lower()
        ]

    total = len(all_sessions)
    page = all_sessions[offset : offset + limit]
    return {"sessions": page, "total": total, "offset": offset, "limit": limit}


@router.delete("/{session_name}")
async def delete_session(session_name: str):
    """Delete a saved session file.

    Removes every on-disk file that belongs to the logical session
    (``foo.kohakutr.v2`` plus its ``foo.kohakutr`` v1 rollback when
    both exist). Falls back to fuzzy lookup if the user passes a
    legacy raw stem.
    """
    try:
        deleted_paths = await asyncio.to_thread(delete_session_files, session_name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")

    if not deleted_paths:
        raise HTTPException(
            status_code=404, detail=f"Session not found: {session_name}"
        )
    return {
        "status": "deleted",
        "name": session_name,
        "files": [p.name for p in deleted_paths],
    }
