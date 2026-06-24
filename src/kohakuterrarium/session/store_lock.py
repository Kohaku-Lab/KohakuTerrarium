"""Cross-process writer lock for :class:`~kohakuterrarium.session.store.SessionStore`.

Extracted from ``store.py`` (which sits at the file-size cap) so the
writer-exclusivity policy is one cohesive unit.

A live engine opens its session with ``writer_lock=True`` so a second
writer — another process, or a second engine in this one — is refused
with :class:`SessionLockedError` rather than silently corrupting the
session (overlapping conversation-snapshot writes + colliding event
counters). Read-only / viewer / listing opens never take the lock, so a
running session stays viewable. The OS frees the lock when the holder
exits or crashes, so a killed process never wedges the next opener.
"""

from kohakuterrarium.errors import SessionLockedError
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy


def acquire_writer_lock(path: str) -> FileLock:
    """Acquire the writer lock for session ``path``.

    Returns the held :class:`FileLock` (release it on store close). Raises
    :class:`SessionLockedError` — annotated with the holder pid — when the
    lock is already held by another writer.
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


def close_tables(tables, fts, lock: "FileLock | None") -> None:
    """Close every KVault table, drop native handles, release the lock.

    The lock release runs in ``finally`` so a failing ``table.close()``
    can never strand the writer lock. Dropping the native ``_KVault`` /
    ``_vault`` handles is required on Windows: ``KVault.close()`` leaves
    the SQLite handle open until GC (and ``TextVault`` has no ``close()``
    at all), which keeps the ``.kohakutr`` locked so a later delete /
    rename fails with WinError 32.
    """
    try:
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
