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
for changes made outside the application. Concurrent refreshes are
single-flighted: bursts queue behind the running scan and at most one
trailing scan runs for the whole burst (see ``_reconcile_guarded``), while
every refresh still reflects the changes that made the client ask.

The router mounts under both ``/api/persistence/saved`` and ``/api/sessions``
to preserve the session API URLs.
"""

import os
import threading

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

# Per-session-directory reconcile state: a lock serialising scans, plus a
# count of scans started (freshness marker — see ``_reconcile_guarded``).
# Entries are never evicted, but the key set is the set of distinct session
# directories this process has listed — a handful in every real deployment.
_RECONCILE_LOCKS: dict[str, threading.Lock] = {}
_RECONCILE_STARTED: dict[str, int] = {}
# Guards creation of the per-directory entries (get-or-create itself is
# racy without it).
_RECONCILE_STATE_LOCK = threading.Lock()


def _reconcile_lock_for(key: str) -> threading.Lock:
    """Return the get-or-create per-directory serialising lock."""
    with _RECONCILE_STATE_LOCK:
        lock = _RECONCILE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _RECONCILE_LOCKS[key] = lock
        return lock


def _reconcile_guarded(session_dir, index, *, full_rescan: bool) -> None:
    """Run one reconcile for a burst of concurrent refresh requests.

    Every reconcile opens session stores (~20 descriptors each), so a
    burst of forced refreshes each running its own directory scan could
    exhaust the process descriptor budget and slow the API for everyone.
    Concurrent callers serialise on the per-directory lock, and a caller
    skips its own pass only once a scan **started after its arrival** has
    run — that scan's snapshot is at least as fresh as the request, so a
    burst of K refreshes costs at most two scans while ``refresh=true``
    still always reflects the changes that made the client ask. A refresh
    that arrives while no scan is running always scans. ``full_rescan``
    keeps its explicit reread-everything intent and never skips.

    A scan that produced no index update — ``reconcile`` reporting
    ``aborted`` (directory-walk failure) or raising — does not count as
    that fresher scan: the start is rolled back so queued waiters run
    their own pass instead of skipping on a start that reflected no work.
    """
    key = os.path.normcase(str(session_dir))
    lock = _reconcile_lock_for(key)
    # Scans started before our arrival cannot reflect the change that
    # made us ask for a refresh; scans started after it can.
    started_at_arrival = _RECONCILE_STARTED.get(key, 0)
    with lock:
        if not full_rescan and _RECONCILE_STARTED.get(key, 0) > started_at_arrival:
            return
        # Mark the start before scanning so waiters compare against this
        # scan, not against a count that lags the work being done.
        _RECONCILE_STARTED[key] = _RECONCILE_STARTED.get(key, 0) + 1
        try:
            report = reconcile(index, session_dir, full=full_rescan)
        except BaseException:
            _RECONCILE_STARTED[key] -= 1
            raise
        if getattr(report, "aborted", False):
            _RECONCILE_STARTED[key] -= 1


@router.get("/disk-usage")
async def get_disk_usage():
    """Return separate session, retained-artifact, and total disk usage.

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
        _reconcile_guarded(session_dir, index, full_rescan=full_rescan)
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
