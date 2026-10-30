"""Enforce single-writer session access across processes.

Read-only consumers remain lock-free, and operating-system lock ownership is
released automatically when a process exits.
"""

from collections.abc import Callable, Iterable
from typing import Any

from kohakuterrarium.errors import SessionLockedError
from kohakuterrarium.session.vault_handles import dispose
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# SessionStore KVault table attributes in close order; the FTS TextVault is
# handled apart.
TABLE_ATTRS: tuple[str, ...] = (
    "events",
    "meta",
    "state",
    "channels",
    "subagents",
    "jobs",
    "conversation",
    "turn_rollup",
)


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
) -> bool:
    """Close companions (LIFO), then every table and the FTS index; release the lock.

    A table close failure propagates after the remaining tables were attempted.
    The writer lock is released even if cleanup fails. Return True only when
    companions succeeded and every native handle was released.
    """
    released = True
    primary: BaseException | None = None
    try:
        for closer in reversed(list(companion_closers)):
            try:
                closer()
            except Exception:
                released = False
                logger.warning("Companion closer failed at close", exc_info=True)
        for table in (*tables, fts):
            if table is None:
                continue
            try:
                if not dispose(table):
                    released = False
            except BaseException as exc:
                released = False
                if primary is None:
                    primary = exc
        if primary is not None:
            raise primary
    except BaseException:
        released = False
        raise
    finally:
        release_writer_lock(lock)
    return released


def discard_partial_open(handles: Iterable[tuple[str, Any]], path: str) -> bool:
    """Release handles opened before a store construction failure.

    ``handles`` pairs every expected attribute name with its handle, or ``None``
    when the failure happened before it opened. Every step is best-effort so one
    failing handle cannot keep the others alive. Return True only after every
    expected handle was assigned and released.
    """
    released = True
    for name, table in handles:
        if table is None:
            released = False
            continue
        try:
            if not dispose(table):
                released = False
        except Exception:
            released = False
            logger.warning(
                "partial-open cleanup step failed",
                table=name,
                path=path,
                exc_info=True,
            )
    return released
