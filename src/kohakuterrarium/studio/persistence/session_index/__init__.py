"""Session index sidecar — central listing + FTS5 search.

The KohakuVault sidecar stores session listing entries, full-text search rows,
and reconciliation metadata in one SQLite file. It avoids reopening every
session database for cold listings.

The process cache lock protects construction and bootstrap only. Per-request
reads and writes rely on SQLite WAL and KohakuVault busy retries. The
reconciliation function remains available from the ``reconcile`` submodule
rather than this package namespace so the submodule attribute is not shadowed.
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

# Re-exporting a function named ``reconcile`` would shadow the submodule on the
# package object. Callers import that function from its submodule directly.
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


# Keeping the derived sidecar beside sessions makes directory moves
# self-contained.
_SIDECAR_NAME = ".kt-index.kvault"

# Completed bootstraps use incremental reconciliation on later starts.
_BOOTSTRAP_FLAG = "bootstrap_completed"


def sidecar_path_for(session_dir: Path) -> Path:
    """Return the canonical sidecar path for a session directory."""
    return session_dir / _SIDECAR_NAME


_singletons: dict[str, SessionIndex] = {}
_singleton_lock = threading.Lock()


def _default_session_dir() -> Path:
    """Resolve the default session directory without importing store helpers.

    ``KT_SESSION_DIR`` takes precedence over the configured application
    directory. Keeping this resolver local avoids a circular dependency;
    callers may pass an explicit directory when other override seams apply.
    """
    env = os.environ.get("KT_SESSION_DIR")
    if env:
        return Path(env)
    return config_dir() / "sessions"


def get_session_index_default(session_dir: Path | None = None) -> SessionIndex:
    """Return the lazily opened process-wide index for ``session_dir``.

    A new sidecar receives a full reconciliation. Later process starts perform
    an incremental fingerprint reconciliation so sessions changed by sibling
    processes while the server was down are discovered. Push hooks and explicit
    refreshes handle drift during the current process lifetime. Each normalized
    session directory owns an independent cached index.
    """
    if session_dir is None:
        session_dir = _default_session_dir()
    normalized_dir = Path(session_dir).expanduser().resolve(strict=False)
    cache_key = os.path.normcase(str(normalized_dir))
    with _singleton_lock:
        cached = _singletons.get(cache_key)
        if cached is not None:
            return cached
        sidecar = sidecar_path_for(normalized_dir)
        instance = SessionIndex(sidecar)
        is_first_bootstrap = instance.meta_get(_BOOTSTRAP_FLAG) != "1"
        try:
            if is_first_bootstrap:
                logger.info(
                    "Bootstrapping session index from disk (full)",
                    path=str(sidecar),
                )
                _run_reconcile(instance, normalized_dir, full=True)
                instance.meta_put(_BOOTSTRAP_FLAG, "1")
            else:
                logger.debug(
                    "Reconciling session index on startup (incremental)",
                    path=str(sidecar),
                )
                _run_reconcile(instance, normalized_dir, full=False)
        except Exception as exc:  # noqa: BLE001
            # Startup remains available with stale index data; the error is
            # logged because later refreshes may recover it.
            logger.error(
                "session index startup reconcile failed; serving stale data",
                error=str(exc),
                first_bootstrap=is_first_bootstrap,
                exc_info=True,
            )
        _singletons[cache_key] = instance
        return instance


def close_session_index() -> None:
    """Idempotently release every cached index's SQLite handles."""
    with _singleton_lock:
        cached = list(_singletons.items())
        _singletons.clear()
        for cache_key, instance in cached:
            try:
                instance.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "close_session_index failed",
                    path=cache_key,
                    error=str(exc),
                    exc_info=True,
                )


def _reset_singleton_for_tests() -> None:
    """Drop cached indexes without closing files that tests may have removed.

    This intentionally may leak handles and is restricted to isolated tests.
    """
    with _singleton_lock:
        _singletons.clear()
