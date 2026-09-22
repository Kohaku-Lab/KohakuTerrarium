"""Shared scan coordination for consumers of a session-directory index."""

import os
import threading
from pathlib import Path

from kohakuterrarium.studio.persistence.session_index.reconcile import reconcile

# Per-session-directory reconcile state: a lock serialising scans, plus a
# count of scans started (freshness marker — see ``reconcile_guarded``).
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


def reconcile_guarded(session_dir, index, *, full_rescan: bool) -> None:
    """Run one reconcile for a burst of concurrent refresh requests.

    Every reconcile opens read snapshots of changed session files, so a
    burst of forced refreshes each running its own directory scan would
    multiply filesystem work and slow the API for everyone.
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
    key = os.path.normcase(str(Path(session_dir).expanduser().resolve(strict=False)))
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
