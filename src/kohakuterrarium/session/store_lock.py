"""Enforce single-writer session access across processes.

Read-only consumers remain lock-free, and operating-system lock ownership is
released automatically when a process exits.
"""

from collections.abc import Callable, Iterable
from typing import Any

from kohakuterrarium.errors import SessionLockedError
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


def discard_partial_open(tables: Iterable[tuple[str, Any]], fts, path: str) -> None:
    """Release tables opened before a store construction failure.

    ``tables`` pairs each attribute name with its KVault, or ``None`` when the
    failure happened before that table opened. Every step is best-effort so one
    failing table cannot keep the others, or the events cache daemon, alive.
    """
    for name, table in tables:
        if table is None:
            continue
        for step in (table.disable_cache, table.close):
            try:
                step()
            except Exception:
                logger.warning(
                    "partial-open cleanup step failed",
                    table=name,
                    path=path,
                    exc_info=True,
                )
        try:
            del table._inner
        except AttributeError:
            pass
    if fts is None:
        return
    try:
        fts.close()
    except Exception:
        logger.warning(
            "partial-open cleanup step failed", table="fts", path=path, exc_info=True
        )
    try:
        del fts._vault
    except AttributeError:
        pass
