"""Persistence saved — list / delete saved sessions.

Listings use the SessionIndex sidecar at
``<session_dir>/.kt-index.kvault``. The persistent SQLite index keeps listing
cost to one file open and one table scan instead of opening every
``.kohakutr`` file.

Queries use FTS5 BM25 ranking. Exact-match facets such as ``status``,
``config_type``, and ``node_id`` filter the FTS hit set without changing its
relevance scores.

``refresh=true`` incrementally reconciles only files whose ``(mtime, size)``
fingerprint changed. ``full_rescan=true`` rereads every file and is intended
for changes made outside the application.

The router mounts under both ``/api/persistence/saved`` and ``/api/sessions``
to preserve the session API URLs.
"""

import os

from fastapi import APIRouter, HTTPException

from kohakuterrarium.api.routes.persistence._executor import (
    run_in_persistence_executor,
)
from kohakuterrarium.studio.persistence.session_index import (
    aggregate_stats,
    get_session_index_default,
)
from kohakuterrarium.studio.persistence.session_index.reconcile import reconcile
from kohakuterrarium.studio.persistence.store import (
    _session_dir,
    delete_session_files,
    disk_usage,
)

router = APIRouter()


@router.get("/disk-usage")
async def get_disk_usage():
    """Return disk usage for canonical session files and SQLite sidecars.

    The filesystem-only directory walk runs on the dedicated persistence
    executor so it cannot occupy the default thread pool shared by unrelated
    event-loop work.
    """
    return await run_in_persistence_executor(disk_usage)


@router.get("/stats")
async def get_session_stats():
    """Return aggregations from the cached session-index sidecar.

    No session store is opened. The synchronous KVault scan runs on the
    persistence executor to keep it off the event loop.
    """
    return await run_in_persistence_executor(_stats_via_index)


def _stats_via_index() -> dict:
    """Read aggregate statistics through the configured session index.

    Passing ``_session_dir()`` explicitly keeps the index singleton aligned
    with runtime or test overrides of the session directory.
    """
    session_dir = _session_dir()
    index = get_session_index_default(session_dir)
    return aggregate_stats(index)


def _list_via_index(
    *,
    search: str,
    sort: str,
    order: str,
    status: str | None,
    config_type: str | None,
    node_id: str | None,
    limit: int,
    offset: int,
    refresh: bool,
    full_rescan: bool,
) -> dict:
    """List indexed sessions through one synchronous executor entrypoint.

    Passing ``_session_dir()`` explicitly keeps the index singleton aligned
    with runtime or test overrides of the session directory.
    """
    session_dir = _session_dir()
    index = get_session_index_default(session_dir)
    if refresh or full_rescan:
        reconcile(index, session_dir, full=full_rescan)
    page = index.list(
        search=search,
        status=status,
        config_type=config_type,
        node_id=node_id,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    result = page.to_dict()
    # Only local working directories can be checked from this host. Remote
    # workers report their own ``pwd_exists`` value when resuming.
    for row in result.get("sessions", []):
        if row.get("node_id"):
            continue
        pwd = row.get("pwd") or ""
        row["pwd_exists"] = (not pwd) or os.path.isdir(pwd)
    return result


@router.get("")
async def list_sessions(
    limit: int = 20,
    offset: int = 0,
    search: str = "",
    refresh: bool = False,
    full_rescan: bool = False,
    sort: str = "last_active",
    order: str = "desc",
    status: str | None = None,
    config_type: str | None = None,
    node_id: str | None = None,
):
    """List indexed sessions with search, sorting, facets, and pagination.

    ``search`` covers name, preview, config path, agents, and working directory.
    ``sort=relevance`` uses BM25 order; other sort fields reorder the matching
    set. ``refresh`` reconciles changed fingerprints before listing, while
    ``full_rescan`` rereads every session file to account for external edits.
    """
    return await run_in_persistence_executor(
        _list_via_index,
        search=search,
        sort=sort,
        order=order,
        status=status,
        config_type=config_type,
        node_id=node_id,
        limit=limit,
        offset=offset,
        refresh=refresh,
        full_rescan=full_rescan,
    )


@router.delete("/{session_name}")
async def delete_session(session_name: str):
    """Delete every file belonging to one logical saved session.

    Versioned and rollback files are removed together. Raw stems are accepted
    through fuzzy lookup for session names that omit the canonical suffix.
    """
    try:
        deleted_paths = await run_in_persistence_executor(
            delete_session_files, session_name
        )
    except HTTPException:
        raise
    except (PermissionError, OSError) as e:
        # An open SQLite or WAL handle makes deletion a transient resource
        # conflict rather than an internal server failure.
        raise HTTPException(
            status_code=409,
            detail=f"Session file is in use and cannot be deleted yet: {e}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")

    if not deleted_paths:
        raise HTTPException(
            status_code=404, detail=f"Session not found: {session_name}"
        )
    # File deletion also removes the corresponding session-index entries.
    return {
        "status": "deleted",
        "name": session_name,
        "files": [p.name for p in deleted_paths],
    }
