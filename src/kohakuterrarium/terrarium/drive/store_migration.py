"""Drive sidecar schema, paths, and one-way legacy migration (design §7).

The durable Drive store lives in a sidecar file paired with a session
(``<name>.kohakutr.drives``), not inside the ``.kohakutr`` (Phase-0 addendum:
the same-file "tenth WAL connection" livelocked under resume). This leaf module
holds the sidecar schema, the sidecar-path helpers, the schema-version parser,
and the one-way legacy-same-file migration — everything the durable
:class:`~kohakuterrarium.terrarium.drive.store.SqliteDriveRepository` needs at
open time. Split out of ``drive.store`` to respect the file-size cap; nothing
here imports the repository (store -> store_migration is one-directional).
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

# Additive sidecar tables; ``IF NOT EXISTS`` so an old session file with no
# Drive tables opens cleanly and gains them on first Drive use (no session
# FORMAT_VERSION bump — KVault never touches drive_* names).
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
    """The Drive sidecar file that pairs with a session ``.kohakutr`` file.

    ``<name>.kohakutr`` -> ``<name>.kohakutr.drives`` (design §7, Phase-0
    addendum). The Drive repository lives in its OWN sqlite file, not a tenth
    WAL connection inside the ``.kohakutr``, so the agent's ``save_state``
    (KohakuVault) and the Drive dispatcher never contend on one file's lock.
    """
    return str(session_path) + ".drives"


# The Drive sidecar's own WAL companions, so session deletion can remove the
# whole family (the sidecar is itself a WAL sqlite file).
_DRIVE_SIDECAR_SUFFIXES: tuple[str, ...] = (".drives", ".drives-wal", ".drives-shm")


def drive_sidecar_family(session_path: str | Path) -> list[str]:
    """Every deletable Drive sidecar paired with a session file.

    This deliberately excludes the persistent ``.drives.migrate-lock``. Removing
    that pathname can split lock ownership across two inodes; it must survive
    deletion so recreating the same session name keeps using the same lock.
    """
    base = str(session_path)
    return [base + suffix for suffix in _DRIVE_SIDECAR_SUFFIXES]


# Drive data tables copied by the one-way legacy migration (drive_meta holds
# only schema_version and is (re)seeded on the fresh sidecar, so it is skipped).
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
    """Parse a stored ``drive_meta.schema_version`` into an int (>= 1).

    Raises :class:`ValueError` for a non-integer / out-of-range marker so the
    caller can reject or rebuild rather than opening an unrecognized store.
    """
    parsed = int(str(value).strip())
    if parsed < 1:
        raise ValueError(f"schema version must be >= 1, got {parsed}")
    return parsed


def _sidecar_is_complete(sidecar_path: str, *, timeout_s: float | None = None) -> bool:
    """True only if the sidecar exists AND carries a valid completion marker.

    ``timeout_s`` bounds SQLite's busy wait when this is an acquisition probe;
    callers must also check their monotonic deadline after the probe because the
    database open and scheduler can consume a small amount of non-busy time.
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
    """Move an incomplete migration destination aside so migration can rebuild.

    The bad file is preserved as ``<sidecar>.corrupt`` (any prior quarantine is
    replaced) rather than blindly skipped or silently overwritten (R1-14).
    """
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
    """True if a pre-sidecar ``.kohakutr`` still holds same-file Drive rows."""
    if not Path(kohakutr_path).exists():
        return False
    conn = sqlite3.connect(kohakutr_path)
    try:
        present = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='drives'"
        ).fetchone()
        if present is None:
            return False
        return conn.execute("SELECT 1 FROM drives LIMIT 1").fetchone() is not None
    finally:
        conn.close()


@contextmanager
def _migration_lock(sidecar_path: str):
    """Serialize the quarantine+rebuild across processes and threads (R1-14).

    Yields ``True`` to the one opener that holds the OS lock (it must migrate) and
    ``False`` to any opener that, while waiting, observed a peer complete the
    migration. The lock is a cross-platform OS-level exclusive lock
    (:class:`~kohakuterrarium.utils.file_lock.FileLock`: ``fcntl.flock`` on POSIX,
    ``msvcrt.locking`` on Windows) held via an open handle for the ENTIRE
    migration, so the kernel releases it automatically on holder death — there is
    no unlink-based stale takeover for a replacement / unstamped lock file to
    race. A contending opener polls until it either acquires the handle or sees the
    sidecar finished; a lock still held past ``_MIGRATE_LOCK_TIMEOUT_S`` is a
    live-but-stuck migrator (a dead holder's lock is already freed by the OS), so
    the waiter fails loudly rather than wait forever."""
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
            # SQLite's timeout bounds its busy handler, not connection setup or
            # scheduling, so enforce the wall-clock deadline again afterward.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Drive migration lock {lock.path.name!r} is held past the "
                    "deadline by a live process; refusing to wait longer"
                )
            if complete:
                yield False  # a peer already finished; caller does nothing
                return
            time.sleep(min(_MIGRATE_POLL_S, remaining))
    try:
        yield True
    finally:
        lock.release()


def _reject_invalid_sidecar_marker(sidecar_path: str) -> None:
    """Reject a sidecar carrying a PRESENT-but-invalid schema marker READ-ONLY,
    before any lock / quarantine / rebuild side effect, mirroring the repository's
    open-time schema guard (R1-14).

    A readable sidecar whose ``drive_meta.schema_version`` is unparseable, < 1, or
    a future version raises :class:`DriveSchemaVersionError` without moving,
    mutating, or creating anything. An ABSENT marker — no file, zero-byte,
    unreadable, no ``drive_meta`` table, or no ``schema_version`` row — is an
    interrupted destination the caller rebuilds, so this returns silently.
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
    """Remove temp databases abandoned by a killed migration process.

    Called only while holding the persistent migration lock, after the kernel has
    released any dead holder's ownership. SQLite journal/WAL/SHM companions share
    the same ``.migrating.<uuid>`` prefix and are swept with their database.
    """
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
    """One-way copy of legacy same-file ``drive_*`` rows into a fresh sidecar.

    Runs at most once per session: skipped the moment the sidecar exists, and
    made atomic via a UNIQUE temp file + rename so a crash mid-copy re-runs from
    the still-intact legacy ``.kohakutr`` rather than stranding a partial sidecar.
    The whole quarantine+rebuild is serialized by an inter-process/thread lock so
    two concurrent opens never race the temp (R1-14). The legacy tables are left
    in place; this migration never mutates the ``.kohakutr``.
    """
    # A present-but-invalid sidecar marker (malformed / future) is rejected
    # READ-ONLY here, before any lock / quarantine / rebuild side effect, so a
    # newer-format store is never silently destroyed by an older build (R1-14).
    _reject_invalid_sidecar_marker(sidecar_path)
    if _sidecar_is_complete(sidecar_path):
        return
    with _migration_lock(sidecar_path) as acquired:
        # Re-check under the lock: a peer may have completed the migration while
        # this opener waited to acquire it (or already did, ``acquired=False``).
        if not acquired:
            return
        _sweep_abandoned_migration_temps(sidecar_path)
        if _sidecar_is_complete(sidecar_path):
            return
        # An existing but incomplete/interrupted destination is quarantined and
        # rebuilt from the still-intact legacy rows rather than blindly skipped.
        if any(Path(sidecar_path + suffix).exists() for suffix in ("", "-wal", "-shm")):
            _quarantine_incomplete_sidecar(sidecar_path)
        if not _legacy_same_file_drives(kohakutr_path):
            return
        _rebuild_sidecar_from_legacy(kohakutr_path, sidecar_path)


def _rebuild_sidecar_from_legacy(kohakutr_path: str, sidecar_path: str) -> None:
    """Copy the legacy same-file ``drive_*`` rows into a fresh sidecar via a
    per-attempt UNIQUE temp file (never a shared ``.migrating`` path, so two
    migrators can never open one temp), then atomically rename into place.

    A failed attempt strands no ``.migrating.<uuid>`` artifact: on any error
    before the rename lands, the temp DB and its SQLite journal companions are
    removed before the error propagates."""
    tmp = Path(sidecar_path + f".migrating.{uuid4().hex}")
    tmp.unlink(missing_ok=True)
    copied = 0
    renamed = False
    try:
        dest = sqlite3.connect(str(tmp), isolation_level=None)
        try:
            dest.executescript(_SCHEMA)
            dest.execute(
                "INSERT INTO drive_meta(key, value) VALUES('schema_version', ?)",
                (str(DRIVE_SCHEMA_VERSION),),
            )
            dest.execute("ATTACH DATABASE ? AS legacy", (kohakutr_path,))
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
