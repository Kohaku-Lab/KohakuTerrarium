"""Leaf helpers for coordinating Drive sidecar migration."""

import time
from contextlib import contextmanager
from pathlib import Path

from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy

_MIGRATE_LOCK_TIMEOUT_S = 30.0
_MIGRATE_POLL_S = 0.02


def drive_migration_lock_path(session_path: str | Path) -> Path:
    """Return the persistent migration-lock path for a session file."""
    return Path(str(session_path) + ".drives.migrate-lock")


@contextmanager
def drive_migration_guard(
    session_path: str | Path,
    *,
    timeout_s: float | None = None,
    poll_s: float | None = None,
):
    """Hold the Drive migration lock with bounded waiting."""
    lock = FileLock(drive_migration_lock_path(session_path))
    deadline = time.monotonic() + (
        _MIGRATE_LOCK_TIMEOUT_S if timeout_s is None else max(timeout_s, 0.0)
    )
    while True:
        try:
            lock.acquire()
            break
        except FileLockBusy:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Drive migration lock {lock.path.name!r} is held past the "
                    "deadline; refusing to modify the session"
                )
            time.sleep(min(_MIGRATE_POLL_S if poll_s is None else poll_s, remaining))
    try:
        yield
    finally:
        lock.release()
