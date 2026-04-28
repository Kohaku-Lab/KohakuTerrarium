"""Persistence saved — list / delete saved sessions.

Routes drain from the legacy ``api/routes/sessions.py``; all logic
lives in ``studio/persistence/store.py``. Mounted under both
``/api/persistence/saved`` and ``/api/sessions`` (URL preservation
for the existing frontend ``sessionAPI`` callers).
"""

from fastapi import APIRouter, HTTPException

from kohakuterrarium.studio.persistence.store import (
    build_session_index,
    delete_session_files,
    get_session_index,
)

router = APIRouter()


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
    """
    if refresh:
        build_session_index()

    all_sessions = get_session_index()

    # Server-side search
    if search:
        q = search.lower()
        all_sessions = [
            s
            for s in all_sessions
            if q
            in " ".join(
                [
                    s.get("name", ""),
                    s.get("config_path", ""),
                    s.get("config_type", ""),
                    s.get("terrarium_name", ""),
                    s.get("preview", ""),
                    s.get("pwd", ""),
                    " ".join(s.get("agents", [])),
                ]
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
        deleted_paths = delete_session_files(session_name)
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
