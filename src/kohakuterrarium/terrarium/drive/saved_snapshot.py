"""Coherent, non-mutating snapshots for saved-session Drive viewing.

The canonical session writer lock excludes framework writers. SQLite's backup API
then copies one transactionally consistent view into a private temporary database;
the viewer never opens the saved sidecar as a writable database and never creates
companions beside it.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from kohakuterrarium.terrarium.drive.errors import DriveConflictError, DriveError
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy


class DriveSidecarMissingError(DriveError):
    """The session has no Drive sidecar (a valid, empty saved Drive view)."""


class OfflineDriveSnapshot:
    """Own the private SQLite connection, temporary database, and writer lock."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        tempdir: tempfile.TemporaryDirectory[str],
        writer_lock: FileLock,
    ) -> None:
        self.connection = connection
        self._tempdir = tempdir
        self._writer_lock = writer_lock

    def close(self) -> None:
        try:
            self.connection.close()
        finally:
            try:
                self._tempdir.cleanup()
            finally:
                self._writer_lock.release()


def _open_source(sidecar: Path, busy_timeout_ms: int) -> sqlite3.Connection:
    if not sidecar.is_file():
        raise DriveSidecarMissingError(f"Drive sidecar {str(sidecar)!r} does not exist")
    wal = Path(str(sidecar) + "-wal")
    shm = Path(str(sidecar) + "-shm")
    if wal.exists() and not shm.is_file():
        raise DriveConflictError(
            f"Drive sidecar {str(sidecar)!r} has a WAL but no readable SHM; "
            "refusing an incomplete offline snapshot"
        )
    mode = "mode=ro" if wal.exists() else "immutable=1"
    try:
        source = sqlite3.connect(
            sidecar.resolve().as_uri() + f"?{mode}",
            uri=True,
            check_same_thread=False,
            isolation_level=None,
        )
        source.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        source.execute("PRAGMA query_only = ON")
        return source
    except sqlite3.Error as exc:
        raise DriveConflictError(
            f"Drive sidecar {str(sidecar)!r} is not readable as a stable snapshot: {exc}"
        ) from exc


def _backup_snapshot(
    sidecar: Path, busy_timeout_ms: int
) -> tuple[sqlite3.Connection, tempfile.TemporaryDirectory[str]]:
    source = _open_source(sidecar, busy_timeout_ms)
    tempdir = tempfile.TemporaryDirectory(prefix="kt-drive-snapshot-")
    snapshot_path = Path(tempdir.name) / "snapshot.db"
    target: sqlite3.Connection | None = None
    try:
        target = sqlite3.connect(
            snapshot_path,
            check_same_thread=False,
            isolation_level=None,
        )
        target.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        source.backup(target)
        target.execute("PRAGMA query_only = ON")
        target.execute("BEGIN")
        target.execute("SELECT count(*) FROM sqlite_master").fetchone()
        target.execute("COMMIT")
        return target, tempdir
    except sqlite3.Error as exc:
        if target is not None:
            target.close()
        tempdir.cleanup()
        raise DriveConflictError(
            f"Drive sidecar {str(sidecar)!r} changed or was busy during snapshot: {exc}"
        ) from exc
    finally:
        source.close()


def open_offline_drive_snapshot(
    session_path: str | Path,
    sidecar_path: str | Path,
    *,
    busy_timeout_ms: int,
) -> OfflineDriveSnapshot:
    """Capture one SQLite-consistent saved view, or fail with a typed Drive error."""
    session = Path(session_path)
    sidecar = Path(sidecar_path)
    writer_lock = FileLock(str(session) + ".lock")
    try:
        writer_lock.acquire()
    except FileLockBusy as exc:
        raise DriveConflictError(
            f"Session {str(session)!r} is open by a live writer; offline view refused"
        ) from exc
    try:
        connection, tempdir = _backup_snapshot(sidecar, busy_timeout_ms)
        return OfflineDriveSnapshot(connection, tempdir, writer_lock)
    except BaseException:
        writer_lock.release()
        raise


__all__ = [
    "DriveSidecarMissingError",
    "OfflineDriveSnapshot",
    "open_offline_drive_snapshot",
]
