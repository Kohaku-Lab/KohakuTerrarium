"""Own SQLite connections and migration state for authentication data.

Each caller receives a distinct connection so transaction ownership stays local.
Every connection enables foreign keys and WAL because those guarantees are not
fully inherited from prior opens. ``KT_AUTH_DB`` overrides the default config path.
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
    """Resolve the live auth database path with ``KT_AUTH_DB`` precedence."""
    explicit = os.environ.get("KT_AUTH_DB")
    if explicit:
        return Path(explicit)
    return config_dir() / "auth.db"


def open_connection(path: Path | None = None) -> sqlite3.Connection:
    """Open a caller-owned connection with foreign keys, WAL, and named rows."""
    target = path or auth_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        target,
        # Thread handoff is permitted, but each request retains its own connection
        # so transactions are never shared across concurrent request work.
        check_same_thread=False,
        isolation_level=None,  # Handlers delimit transactions explicitly.
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Close a caller-owned connection without masking an active handler error."""
    conn = open_connection(path)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive
            logger.warning("auth.db: connection close raised", exc_info=True)


# Migrations run at most once per resolved database path in each process.

_migration_lock = threading.Lock()
_migrated_paths: set[str] = set()


def ensure_migrated(path: Path | None = None) -> Path:
    """Apply pending migrations once per resolved database path and process."""
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
    """Clear cached migration paths so isolated databases can migrate again."""
    with _migration_lock:
        _migrated_paths.clear()


__all__ = [
    "auth_db_path",
    "connection",
    "ensure_migrated",
    "open_connection",
]
