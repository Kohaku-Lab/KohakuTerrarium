"""Enforce single-writer session access across processes.

Read-only consumers remain lock-free, and operating-system lock ownership is
released automatically when a process exits.
"""

from collections.abc import Callable, Iterable

from kohakuterrarium.errors import SessionLockedError
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def acquire_writer_lock(path: str) -> FileLock:
    """Acquire the writer lock for session ``path``.

    Return the held lock for release at store close. If another writer owns it,
    raise :class:`SessionLockedError` with the holder process identifier.
    """
    lock = FileLock(path + ".lock")
    try:
        lock.acquire()
    except FileLockBusy as exc:
        raise SessionLockedError(
            f"Session is already open for writing by another process "
            f"(pid {exc.holder_pid}): {path}. Close the other instance, or "
            f"open it read-only to view.",
            holder_pid=exc.holder_pid,
        ) from exc
    return lock


def release_writer_lock(lock: "FileLock | None") -> None:
    """Release ``lock`` if present. Tolerates ``None`` (read-only opens)."""
    if lock is not None:
        lock.release()


def close_tables(
    tables,
    fts,
    lock: "FileLock | None",
    companion_closers: Iterable[Callable[[], None]] = (),
) -> None:
    """Close every KVault table, drop native handles, release the lock.

    Companion resources close first in LIFO order. Native vault handles are
    explicitly dropped because their public close paths can retain SQLite file
    handles on Windows. The writer lock is released even if cleanup fails.
    """
    try:
        for closer in reversed(list(companion_closers)):
            try:
                closer()
            except Exception:
                logger.warning("Companion closer failed at close", exc_info=True)
        for table in tables:
            table.close()
        for table in tables:
            try:
                del table._inner
            except AttributeError:
                pass
        try:
            del fts._vault
        except AttributeError:
            pass
    finally:
        release_writer_lock(lock)
