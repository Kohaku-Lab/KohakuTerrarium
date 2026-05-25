"""Session index sidecar — central listing + FTS5 search.

A single SQLite file at ``<session_dir>/.kt-index.kvault`` (managed
by KohakuVault) replaces the legacy "open every ``.kohakutr`` file
on cold cache" pattern in ``api/sessions``.  The sidecar holds:

* A KVault ``entries`` table — listing-shape metadata per session
  (name, last_active, preview, tags, …).  Source of truth for
  sort + filter queries.
* A TextVault ``search`` table — FTS5 over the searchable text
  columns (name / preview / config_path / agents / pwd) with BM25
  ranking.
* A KVault ``meta`` table — schema version + bootstrap flag +
  last-reconcile timestamp.

Public surface:

* :func:`get_session_index_default` — process-wide singleton.
  First call bootstraps from disk via ``reconcile.reconcile``;
  subsequent calls return the cached instance.
* :func:`close_session_index` — release the singleton's native
  SQLite handles.  Called from the FastAPI lifespan on shutdown.
* :class:`SessionIndex` — the index object itself.  Tests
  construct it directly with a custom path; production callers go
  through the singleton.
* :class:`SessionIndexEntry` / :class:`SessionIndexHook` /
  :class:`ReconcileReport` — re-exported for convenience.
* :mod:`.reconcile` — submodule housing the ``reconcile()`` function
  + ``read_entry_from_disk()`` helper.  Import the function
  explicitly from ``session_index.reconcile`` (NOT re-exported on
  this package to keep the submodule attribute reachable for
  ``monkeypatch.setattr`` calls in tests).

Concurrency: the singleton lock guards the bootstrap, NOT the
per-request reads.  Reads are safe to interleave because KohakuVault
uses SQLite WAL.  Writes (upsert / delete) race-retry inside
KohakuVault; we don't add an extra Python lock.

Why a sidecar (and not just a TTL'd in-memory index): listing 1000
sessions today re-opens 1000 SQLite files on every cold cache.  The
sidecar makes the cold path a single file open + a single table
scan — sub-100 ms regardless of how many sessions exist.  See
``plans/`` for the design discussion.
"""

import os
import threading
from pathlib import Path

from kohakuterrarium.studio.persistence.session_index.entry import (
    SCHEMA_VERSION,
    SessionIndexEntry,
)
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.studio.persistence.session_index.hooks import (
    SessionIndexHook,
    push_index_update,
)
from kohakuterrarium.studio.persistence.session_index.reconcile import (
    ReconcileReport,
    read_entry_from_disk,
    reconcile as _run_reconcile,
)

# IMPORTANT: we deliberately do NOT re-export the ``reconcile``
# function from this ``__init__``.  Binding a function named
# ``reconcile`` here would shadow the ``.reconcile`` submodule on
# the package object (Python attribute resolution), breaking
# ``monkeypatch.setattr("...session_index.reconcile.x", ...)``
# patterns in tests.  Callers import the function from its
# submodule explicitly:
#
#     from kohakuterrarium.studio.persistence.session_index.reconcile \
#         import reconcile
#
# ``ReconcileReport`` and ``read_entry_from_disk`` are safe to
# re-export because their names don't collide with the submodule.
from kohakuterrarium.studio.persistence.session_index.stats import aggregate_stats
from kohakuterrarium.studio.persistence.session_index.store import (
    SEARCH_COLUMNS,
    SessionIndex,
    SessionIndexPage,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "SCHEMA_VERSION",
    "SEARCH_COLUMNS",
    "SessionIndex",
    "SessionIndexEntry",
    "SessionIndexHook",
    "SessionIndexPage",
    "ReconcileReport",
    "aggregate_stats",
    "close_session_index",
    "get_session_index_default",
    "push_index_update",
    "read_entry_from_disk",
    "sidecar_path_for",
]


# Sidecar filename.  Lives next to the session files so cross-host
# moves carry the index along; cheap to delete + rebuild if a user
# wants a clean slate.
_SIDECAR_NAME = ".kt-index.kvault"

# Bootstrap flag — once flipped, future server starts skip the
# (potentially slow) full reconcile and rely on the incremental
# reconcile that ``?refresh=true`` invokes.
_BOOTSTRAP_FLAG = "bootstrap_completed"


def sidecar_path_for(session_dir: Path) -> Path:
    """The canonical sidecar location for a given session directory."""
    return session_dir / _SIDECAR_NAME


# ──────────────────────────────────────────────────────────────────
# Process-wide singleton
# ──────────────────────────────────────────────────────────────────

_singleton: SessionIndex | None = None
_singleton_dir: Path | None = None
_singleton_lock = threading.Lock()


def _default_session_dir() -> Path:
    """Standalone session-dir resolver.

    Honours ``KT_SESSION_DIR`` first (the documented runtime
    override), then falls back to ``config_dir() / "sessions"``.
    Mirrors ``studio.persistence.store._session_dir`` but lives
    here so this package doesn't have to import the legacy listing
    module to discover its own default — that would create a
    circular dep.

    Production callers (the API route) pass the legacy module's
    ``_session_dir()`` explicitly so test monkeypatches on
    ``store._SESSION_DIR`` continue to win.
    """
    env = os.environ.get("KT_SESSION_DIR")
    if env:
        return Path(env)
    return config_dir() / "sessions"


def get_session_index_default(session_dir: Path | None = None) -> SessionIndex:
    """Return the process-wide :class:`SessionIndex`, opening it lazily.

    The ``session_dir`` argument is normally passed by the API
    route — it resolves via ``studio.persistence.store._session_dir``
    (which honours the legacy ``_SESSION_DIR`` monkey-patch seam +
    ``KT_SESSION_DIR`` env var).  Passing ``None`` falls back to
    :func:`_default_session_dir` so tests + CLI tools can use the
    singleton without dragging the legacy module in.

    Sync on first open per process:

    * Sidecar missing / never bootstrapped → full reconcile (every
      file read), then ``bootstrap_completed`` flag set.
    * Sidecar exists but server was previously down → **incremental**
      reconcile: fingerprint-diff every file, only re-read the ones
      that changed (sub-second on a 1000-session install where
      nothing changed; catches new sessions that a ``kt run`` or
      another process produced while this server was down).

    The incremental path on every fresh start is what keeps the
    sidecar honest across server restarts + cross-process activity
    (``kt run`` writes its session as a sibling process; its
    SessionStore doesn't reach the server's push-hook subscriber).
    Within a single server lifetime, push hooks + the
    ``?refresh=true`` route handle further drift.
    """
    global _singleton, _singleton_dir
    if session_dir is None:
        session_dir = _default_session_dir()
    with _singleton_lock:
        if _singleton is not None and _singleton_dir == session_dir:
            return _singleton
        # If we're switching directories (test override, KT_SESSION_DIR
        # changed), close the old singleton first so its native SQLite
        # handles don't leak.
        if _singleton is not None:
            try:
                _singleton.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("singleton close-on-rotate failed", error=str(exc))
        sidecar = sidecar_path_for(session_dir)
        instance = SessionIndex(sidecar)
        is_first_bootstrap = instance.meta_get(_BOOTSTRAP_FLAG) != "1"
        try:
            if is_first_bootstrap:
                logger.info(
                    "Bootstrapping session index from disk (full)",
                    path=str(sidecar),
                )
                _run_reconcile(instance, session_dir, full=True)
                instance.meta_put(_BOOTSTRAP_FLAG, "1")
            else:
                # Cheap fingerprint-diff sync — catches sessions
                # added / deleted / mutated by other processes
                # (e.g. ``kt run``) while this server was down.
                logger.debug(
                    "Reconciling session index on startup (incremental)",
                    path=str(sidecar),
                )
                _run_reconcile(instance, session_dir, full=False)
        except Exception as exc:  # noqa: BLE001
            # Reconcile failure should not block the server from
            # starting — the index serves whatever state it had.
            # Log loudly so it doesn't silently degrade.
            logger.error(
                "session index startup reconcile failed; serving stale data",
                error=str(exc),
                first_bootstrap=is_first_bootstrap,
                exc_info=True,
            )
        _singleton = instance
        _singleton_dir = session_dir
        return instance


def close_session_index() -> None:
    """Release the singleton's SQLite handles.  Idempotent."""
    global _singleton, _singleton_dir
    with _singleton_lock:
        if _singleton is None:
            return
        try:
            _singleton.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("close_session_index failed", error=str(exc))
        _singleton = None
        _singleton_dir = None


def _reset_singleton_for_tests() -> None:
    """Test hook: drop the singleton without trying to close (file may be gone).

    Production code must not call this — it skips ``close`` and so
    can leak handles.  Tests use ``KT_CONFIG_DIR`` per-fixture and need
    a clean slate between cases.
    """
    global _singleton, _singleton_dir
    with _singleton_lock:
        _singleton = None
        _singleton_dir = None
