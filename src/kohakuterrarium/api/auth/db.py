"""sqlite connection factory + lifecycle for the auth DB.

Single connection per request — sqlite3 is thread-safe (with the
appropriate ``check_same_thread=False``) when used carefully; we
deliberately use one connection per call to keep transaction
ownership obvious in the route handlers.

The DB file lives at ``<config_dir>/auth.db``.  When ``KT_AUTH_DB``
is set, that path is used instead (tests / Docker volume binds rely
on this).  ``WAL`` + ``foreign_keys ON`` are set on every connection
open because sqlite forgets the foreign-keys pragma per-connection.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from kohakuterrarium.api.auth.migrations import run_migrations
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def auth_db_path() -> Path:
    """Resolve the auth.db location fresh on every call.

    ``KT_AUTH_DB`` overrides; else falls back to
    ``<config_dir>/auth.db``.  Honouring the env var on every call
    keeps tests' tmp-dir isolation intact (the autouse fixture in
    ``tests/conftest.py`` redirects ``KT_CONFIG_DIR`` per test).
    """
    explicit = os.environ.get("KT_AUTH_DB")
    if explicit:
        return Path(explicit)
    return config_dir() / "auth.db"


def open_connection(path: Path | None = None) -> sqlite3.Connection:
    """Open a fresh sqlite connection with KT's standard pragmas.

    - ``foreign_keys = ON`` so ``ON DELETE CASCADE`` actually cascades.
    - ``journal_mode = WAL`` for concurrent readers + writers.
    - ``row_factory = sqlite3.Row`` so handlers can index by column name.

    The caller owns the connection and must close it.  For request-scoped
    use, :func:`connection` is a context-manager wrapper.
    """
    target = path or auth_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        target,
        # check_same_thread=False so the dependency can hand off the
        # conn to background tasks if needed.  We still keep one conn
        # per request to dodge cross-thread transaction confusion.
        check_same_thread=False,
        isolation_level=None,  # autocommit; route handlers wrap explicit txns
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Context-manager wrapper around :func:`open_connection`.

    Closes the connection on exit, swallowing close errors so the
    actual handler exception (if any) propagates.
    """
    conn = open_connection(path)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive
            logger.debug("auth.db: connection close raised", exc_info=True)


# ---------------------------------------------------------------------------
# Process-level migration state — applied once per (db path) per process.
# ---------------------------------------------------------------------------

_migration_lock = threading.Lock()
_migrated_paths: set[str] = set()


def ensure_migrated(path: Path | None = None) -> Path:
    """Apply pending migrations against the resolved DB path.

    Called from the FastAPI lifespan startup hook (Phase F) so the DB
    is ready before any request hits the auth routes.  Idempotent
    in-process: the first call for a given path runs migrations, every
    subsequent call short-circuits.
    """
    target = path or auth_db_path()
    key = str(target.resolve())
    with _migration_lock:
        if key in _migrated_paths:
            return target
        with connection(target) as conn:
            run_migrations(conn)
        _migrated_paths.add(key)
    return target


def _reset_migration_state_for_tests() -> None:
    """Drop the in-process migration cache.

    Test fixtures call this between cases when they flip ``KT_AUTH_DB``
    so the next ``ensure_migrated`` re-runs against the new file.
    """
    with _migration_lock:
        _migrated_paths.clear()


__all__ = [
    "auth_db_path",
    "connection",
    "ensure_migrated",
    "open_connection",
]
