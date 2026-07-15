"""Drive sidecar schema, paths, validation, and legacy migration.

Drive sidecar schema, path helpers, validation, and legacy migration.
"""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from kohakuterrarium.terrarium.drive.errors import DriveSchemaVersionError
from kohakuterrarium.utils.drive_migration_lock import (
    _MIGRATE_LOCK_TIMEOUT_S,
    _MIGRATE_POLL_S,
)
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

DRIVE_SCHEMA_VERSION = 1

# Additive tables let existing sessions initialize Drive storage on first use.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS drive_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS drives (drive_id TEXT PRIMARY KEY, blob TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drive_assignments (
    drive_id TEXT PRIMARY KEY, blob TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drive_deliveries (
    delivery_id TEXT PRIMARY KEY, drive_id TEXT NOT NULL, blob TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_deliveries_drive ON drive_deliveries(drive_id);
CREATE TABLE IF NOT EXISTS drive_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, drive_id TEXT NOT NULL,
    blob TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_audit_drive ON drive_audit(drive_id);
CREATE TABLE IF NOT EXISTS drive_progress (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, drive_id TEXT NOT NULL,
    blob TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_progress_drive ON drive_progress(drive_id);
CREATE TABLE IF NOT EXISTS drive_idempotency (
    actor TEXT NOT NULL, key TEXT NOT NULL, operation_hash TEXT NOT NULL,
    result_blob TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (actor, key));
CREATE TABLE IF NOT EXISTS drive_outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, outbox_id TEXT NOT NULL UNIQUE,
    drive_id TEXT NOT NULL, dispatched INTEGER NOT NULL DEFAULT 0,
    blob TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drive_dead_letters (
    delivery_id TEXT PRIMARY KEY, drive_id TEXT NOT NULL, blob TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drive_proposals (
    proposal_id TEXT PRIMARY KEY, drive_id TEXT NOT NULL, blob TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_proposals_drive ON drive_proposals(drive_id);
"""


def drive_sidecar_path(session_path: str | Path) -> str:
    """Return the dedicated Drive sidecar path paired with a session file.

    Keeping Drive storage separate prevents session and dispatcher writes from
    contending on the same SQLite file.
    """
    return str(session_path) + ".drives"


# Session deletion includes the sidecar's WAL and shared-memory companions.
_DRIVE_SIDECAR_SUFFIXES: tuple[str, ...] = (".drives", ".drives-wal", ".drives-shm")


def drive_sidecar_family(session_path: str | Path) -> list[str]:
    """Return every deletable Drive sidecar file paired with a session.

    The persistent migration lock is excluded because unlinking it can split lock
    ownership across different inodes.
    """
    base = str(session_path)
    return [base + suffix for suffix in _DRIVE_SIDECAR_SUFFIXES]


# Migration reseeds metadata, so only data tables are copied from legacy storage.
_DRIVE_DATA_TABLES = (
    "drives",
    "drive_assignments",
    "drive_deliveries",
    "drive_audit",
    "drive_progress",
    "drive_idempotency",
    "drive_outbox",
    "drive_dead_letters",
    "drive_proposals",
)


def _parse_schema_version(value: Any) -> int:
    """Parse a positive integer Drive schema version."""
    parsed = int(str(value).strip())
    if parsed < 1:
        raise ValueError(f"schema version must be >= 1, got {parsed}")
    return parsed


def _sidecar_is_complete(sidecar_path: str, *, timeout_s: float | None = None) -> bool:
    """Return whether a sidecar exists with a valid schema marker.

    ``timeout_s`` bounds SQLite's busy wait, but callers must separately enforce
    wall-clock deadlines because opening and scheduling also consume time.
    """
    p = Path(sidecar_path)
    if not p.exists() or p.stat().st_size == 0:
        return False
    probe_deadline = (
        None if timeout_s is None else time.monotonic() + max(timeout_s, 0.0)
    )

    def _remaining() -> float:
        if probe_deadline is None:
            return 5.0
        return max(probe_deadline - time.monotonic(), 0.0)

    try:
        conn = sqlite3.connect(sidecar_path, timeout=_remaining())
    except sqlite3.Error:
        return False
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(_remaining() * 1000)}")
        marker = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='drive_meta'"
        ).fetchone()
        if marker is None or (probe_deadline is not None and _remaining() <= 0):
            return False
        conn.execute(f"PRAGMA busy_timeout = {int(_remaining() * 1000)}")
        row = conn.execute(
            "SELECT value FROM drive_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return False
        _parse_schema_version(row[0])
        return True
    except (sqlite3.DatabaseError, ValueError):
        return False
    finally:
        conn.close()


def _quarantine_incomplete_sidecar(sidecar_path: str) -> None:
    """Quarantine an incomplete sidecar so migration can rebuild it."""
    if not any(Path(sidecar_path + suffix).exists() for suffix in ("", "-wal", "-shm")):
        return
    for suffix in ("", "-wal", "-shm"):
        source = Path(sidecar_path + suffix)
        quarantine = Path(sidecar_path + ".corrupt" + suffix)
        quarantine.unlink(missing_ok=True)
        if not source.exists():
            continue
        try:
            source.replace(quarantine)
        except OSError:
            source.unlink(missing_ok=True)
    logger.warning(
        "Quarantined an incomplete Drive sidecar; rebuilding from legacy rows",
        sidecar=sidecar_path,
    )


def _legacy_same_file_drives(kohakutr_path: str) -> bool:
    """Return whether a session database still contains legacy Drive rows.

    The immutable probe avoids locks and WAL handling while another SQLite
    implementation may hold live connections to the session database.
    """
    path = Path(kohakutr_path)
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?immutable=1", uri=True)
    except sqlite3.Error:
        return False
    try:
        present = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='drives'"
        ).fetchone()
        if present is None:
            return False
        return conn.execute("SELECT 1 FROM drives LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        conn.close()


@contextmanager
def _migration_lock(sidecar_path: str):
    """Serialize sidecar quarantine and rebuild across processes and threads.

    Yields whether the caller acquired the migration lock. Waiters may return
    ``False`` after observing a peer complete migration, and fail on the deadline
    rather than waiting indefinitely for a live but stuck holder.
    """
    lock = FileLock(sidecar_path + ".migrate-lock")
    deadline = time.monotonic() + _MIGRATE_LOCK_TIMEOUT_S
    while True:
        try:
            lock.acquire()
            break
        except FileLockBusy:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Drive migration lock {lock.path.name!r} is held past the "
                    "deadline by a live process; refusing to wait longer"
                )
            complete = _sidecar_is_complete(sidecar_path, timeout_s=remaining)
            # SQLite's busy timeout excludes connection setup and scheduling time.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Drive migration lock {lock.path.name!r} is held past the "
                    "deadline by a live process; refusing to wait longer"
                )
            if complete:
                yield False  # The waiting peer completed migration.
                return
            time.sleep(min(_MIGRATE_POLL_S, remaining))
    try:
        yield True
    finally:
        lock.release()


def _reject_invalid_sidecar_marker(sidecar_path: str) -> None:
    """Reject invalid or unsupported schema markers without mutating the sidecar.

    Missing markers identify incomplete destinations and are left for migration to
    rebuild.
    """
    p = Path(sidecar_path)
    if not p.exists() or p.stat().st_size == 0:
        return
    try:
        conn = sqlite3.connect(sidecar_path)
    except sqlite3.Error:
        return
    try:
        has_meta = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='drive_meta'"
        ).fetchone()
        if has_meta is None:
            return
        row = conn.execute(
            "SELECT value FROM drive_meta WHERE key = 'schema_version'"
        ).fetchone()
    except sqlite3.DatabaseError:
        return
    finally:
        conn.close()
    if row is None:
        return
    try:
        found = _parse_schema_version(row[0])
    except ValueError as exc:
        raise DriveSchemaVersionError(
            f"Drive sidecar {sidecar_path!r} has an unparseable schema version "
            f"{row[0]!r}; refusing to migrate (repair/rebuild required)"
        ) from exc
    if found > DRIVE_SCHEMA_VERSION:
        raise DriveSchemaVersionError(
            f"Drive sidecar {sidecar_path!r} declares schema version {found}, but "
            f"this build supports at most {DRIVE_SCHEMA_VERSION}; a newer build "
            "wrote it (migration required)"
        )


def _sweep_abandoned_migration_temps(sidecar_path: str) -> None:
    """Remove abandoned migration databases and their SQLite companions."""
    sidecar = Path(sidecar_path)
    prefix = sidecar.name + ".migrating."
    for candidate in sidecar.parent.glob(prefix + "*"):
        tail = candidate.name[len(prefix) :]
        attempt_id = tail.split("-", 1)[0]
        if len(attempt_id) != 32 or any(
            c not in "0123456789abcdef" for c in attempt_id
        ):
            continue
        if tail != attempt_id and tail not in {
            attempt_id + "-journal",
            attempt_id + "-wal",
            attempt_id + "-shm",
        }:
            continue
        candidate.unlink(missing_ok=True)


def _migrate_same_file_drives(kohakutr_path: str, sidecar_path: str) -> None:
    """Atomically copy legacy same-file Drive rows into a sidecar.

    A unique temporary database and rename make retries crash-safe. The migration
    lock prevents concurrent rebuilds, and the source session remains unchanged.
    """
    # Validate before locking or quarantine so older builds cannot destroy newer data.
    _reject_invalid_sidecar_marker(sidecar_path)
    if _sidecar_is_complete(sidecar_path):
        return
    with _migration_lock(sidecar_path) as acquired:
        # A peer may complete migration while this opener waits for the lock.
        if not acquired:
            return
        _sweep_abandoned_migration_temps(sidecar_path)
        if _sidecar_is_complete(sidecar_path):
            return
        # Preserve an incomplete destination before rebuilding from the legacy source.
        if any(Path(sidecar_path + suffix).exists() for suffix in ("", "-wal", "-shm")):
            _quarantine_incomplete_sidecar(sidecar_path)
        if not _legacy_same_file_drives(kohakutr_path):
            return
        _rebuild_sidecar_from_legacy(kohakutr_path, sidecar_path)


def _rebuild_sidecar_from_legacy(kohakutr_path: str, sidecar_path: str) -> None:
    """Rebuild a sidecar through a unique temporary database and atomic rename.

    Failed attempts remove the temporary database and all SQLite companions.
    """
    tmp = Path(sidecar_path + f".migrating.{uuid4().hex}")
    tmp.unlink(missing_ok=True)
    copied = 0
    renamed = False
    try:
        # Immutable attachment prevents migration from locking a live session database.
        dest = sqlite3.connect(tmp.resolve().as_uri(), uri=True, isolation_level=None)
        try:
            dest.executescript(_SCHEMA)
            dest.execute(
                "INSERT INTO drive_meta(key, value) VALUES('schema_version', ?)",
                (str(DRIVE_SCHEMA_VERSION),),
            )
            dest.execute(
                "ATTACH DATABASE ? AS legacy",
                (Path(kohakutr_path).resolve().as_uri() + "?immutable=1",),
            )
            dest.execute("BEGIN IMMEDIATE")
            for table in _DRIVE_DATA_TABLES:
                in_legacy = dest.execute(
                    "SELECT name FROM legacy.sqlite_master "
                    "WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if in_legacy is None:
                    continue
                cur = dest.execute(f"INSERT INTO {table} SELECT * FROM legacy.{table}")
                copied += max(cur.rowcount, 0)
            dest.execute("COMMIT")
            dest.execute("DETACH DATABASE legacy")
        finally:
            dest.close()
        tmp.replace(sidecar_path)
        renamed = True
    finally:
        if not renamed:
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(str(tmp) + suffix).unlink(missing_ok=True)
    logger.info(
        "Migrated legacy same-file Drive rows into sidecar",
        rows=copied,
        sidecar=sidecar_path,
    )
