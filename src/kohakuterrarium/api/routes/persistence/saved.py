"""Persistence saved — list / delete saved sessions.

The listing path is backed by the SessionIndex sidecar
(``studio/persistence/session_index``): a single SQLite file at
``<session_dir>/.kt-index.kvault`` cached across server restarts.
Cold-listing 1000 sessions is one file open + one table scan
instead of N parallel ``.kohakutr`` opens.

Search uses FTS5 (BM25) when a query is present; faceted filters
(``status``, ``config_type``, ``node_id``) apply after the FTS
hit-set is collected so the rank stays meaningful.

``refresh=true`` triggers an **incremental** reconcile — every
file whose ``(mtime, size)`` fingerprint matches the sidecar is
skipped without opening it.  Pass ``full_rescan=true`` to force
a re-read of every file (use after a manual disk edit).

Mounted under both ``/api/persistence/saved`` and ``/api/sessions``
(URL preservation for the existing frontend ``sessionAPI`` callers).
"""

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
    """Aggregate disk usage of the saved-session directory.

    Pure filesystem — stats every canonical session file + its
    SQLite sidecars without opening any database. Off-loaded to the
    dedicated persistence executor so the directory walk doesn't
    block the loop's default thread pool (which other ``to_thread``
    calls — chat WS, runtime graph, identity routes — share).
    """
    return await run_in_persistence_executor(disk_usage)


@router.get("/stats")
async def get_session_stats():
    """Aggregations over the session index sidecar.

    Pure read of the cached sidecar — no ``.kohakutr`` is opened.
    Sub-millisecond for ~thousands of sessions; runs on the
    persistence executor because the underlying KVault scan is sync.
    """
    return await run_in_persistence_executor(_stats_via_index)


def _stats_via_index() -> dict:
    """Sync entrypoint — runs on the persistence executor.

    Passes ``_session_dir()`` explicitly so the SessionIndex
    singleton picks up the same path that tests monkeypatch via
    ``store._SESSION_DIR`` + ``KT_SESSION_DIR`` (same pattern
    :func:`_list_via_index` uses).
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
    """Sync entrypoint — runs on the persistence executor.

    Resolved here (not inline in the route) so the executor sees
    a single function call.  Bridges the route's keyword args to
    the SessionIndex API.

    Passes ``_session_dir()`` explicitly so the SessionIndex
    singleton picks up the same path that tests monkeypatch via
    ``store._SESSION_DIR`` + ``KT_SESSION_DIR``.
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
    return page.to_dict()


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
    """List saved sessions with search, sort, filter, pagination.

    Backed by the SessionIndex sidecar.  Cold-list cost is one
    file open regardless of how many sessions exist (vs the
    legacy "open N ``.kohakutr`` files" path).

    Query params:
      * ``search`` — FTS5 query over name / preview / config_path /
        agents / pwd.  When set, ``sort=relevance`` orders by
        BM25 (most relevant first); any other ``sort`` orders the
        FTS hit-set by that field.
      * ``sort`` — ``last_active`` (default) | ``created_at`` |
        ``name`` | ``status`` | ``relevance``.
      * ``order`` — ``desc`` (default) | ``asc``.
      * ``status`` / ``config_type`` / ``node_id`` — exact-match
        facet filters.
      * ``refresh=true`` — incremental reconcile before listing
        (re-reads only files whose mtime/size changed).
      * ``full_rescan=true`` — force-re-read every file regardless
        of fingerprint (use after manual disk edits).
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
    """Delete a saved session file.

    Removes every on-disk file that belongs to the logical session
    (``foo.kohakutr.v2`` plus its ``foo.kohakutr`` v1 rollback when
    both exist). Falls back to fuzzy lookup if the user passes a
    legacy raw stem.
    """
    try:
        deleted_paths = await run_in_persistence_executor(
            delete_session_files, session_name
        )
    except HTTPException:
        raise
    except (PermissionError, OSError) as e:
        # The `.kohakutr` file is locked — typically a still-open
        # SQLite/WAL handle from a session that has not fully released
        # it. That is a transient conflict, not a server fault: 409.
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
    # ``delete_session_files`` itself purges the matching entries
    # from the session-index sidecar — see store._purge_index_entries.
    return {
        "status": "deleted",
        "name": session_name,
        "files": [p.name for p in deleted_paths],
    }
