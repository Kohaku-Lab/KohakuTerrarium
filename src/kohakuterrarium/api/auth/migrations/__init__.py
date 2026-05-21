"""Migration runner — applies ``*.sql`` files in lexical order.

Each migration is wrapped in a transaction.  The :func:`run_migrations`
function:

1. Ensures ``schema_version`` exists (running the schema-bootstrap
   migration 001 if not — that file creates ``schema_version`` plus
   every other table).
2. Reads ``schema_version`` to find the highest applied version.
3. Iterates ``*.sql`` files in this folder, sorted lexically.  For
   each file whose prefix integer is higher than the current version,
   applies it inside a transaction and inserts an audit row in
   ``schema_version``.

The migrations are append-only by convention.  Down-migrations are not
supported — same discipline as KohakuVault.  Sequential numbering
keeps the lexical sort identical to the numeric sort.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent
_MIGRATION_FILE_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def _list_migrations() -> list[tuple[int, Path]]:
    """Return ``(version, path)`` pairs sorted by version."""
    found: list[tuple[int, Path]] = []
    for path in _MIGRATIONS_DIR.glob("*.sql"):
        m = _MIGRATION_FILE_RE.match(path.name)
        if not m:
            logger.warning(
                "auth.migrations: skipping non-conforming file",
                name=path.name,
            )
            continue
        found.append((int(m.group(1)), path))
    found.sort(key=lambda pair: pair[0])
    return found


def _ensure_schema_version_table(conn) -> None:
    """Create ``schema_version`` if missing — bootstrap for a fresh DB.

    The first migration (``001_initial.sql``) also creates this table;
    we re-issue ``CREATE TABLE IF NOT EXISTS`` here so a partially-
    applied first migration doesn't deadlock the runner.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """)
    conn.commit()


def _current_version(conn) -> int:
    """Return the highest applied schema version (0 = fresh)."""
    cur = conn.execute("SELECT MAX(version) FROM schema_version")
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def run_migrations(conn) -> int:
    """Apply every pending migration; return the resulting schema version.

    Idempotent: re-running on an up-to-date DB is a no-op + logged at DEBUG.
    """
    _ensure_schema_version_table(conn)
    current = _current_version(conn)
    pending = [(v, p) for v, p in _list_migrations() if v > current]
    if not pending:
        logger.debug("auth.migrations: nothing to do", current_version=current)
        return current

    for version, path in pending:
        logger.info("auth.migrations: applying", version=version, file=path.name)
        sql = path.read_text(encoding="utf-8")
        try:
            # SQLite's executescript() runs in its own transaction;
            # we manage commits manually so the version-bump row lands
            # in the same atomic step.
            conn.execute("BEGIN")
            conn.executescript(sql)
            # The migration file MAY itself insert into schema_version
            # (001 does so to bootstrap the version row); ensure idempotency
            # by checking before inserting.
            cur = conn.execute(
                "SELECT 1 FROM schema_version WHERE version = ?", (version,)
            )
            if cur.fetchone() is None:
                conn.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                    (version, datetime.now(timezone.utc).isoformat()),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception(
                "auth.migrations: failed; rolled back",
                version=version,
                file=path.name,
            )
            raise

    final = _current_version(conn)
    logger.info("auth.migrations: complete", schema_version=final)
    return final


__all__ = ["run_migrations"]
