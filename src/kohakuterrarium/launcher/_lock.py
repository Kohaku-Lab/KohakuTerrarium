"""Provide a non-blocking cross-platform lock for launcher updates.

POSIX uses ``fcntl.flock`` and Windows uses ``msvcrt.locking``. The lock file
also records holder metadata so interfaces can identify stale update attempts.
"""

import os
import sys
import time
from pathlib import Path
from typing import IO

from kohakuterrarium.launcher.log import get_logger

STALE_LOCK_SECONDS = 10 * 60  # Allow long updates before offering stale recovery.


class LockBusy(RuntimeError):
    """Raised when another process holds the update lock."""


class UpdateLock:
    """Acquire and release the exclusive launcher update lock.

    Entering is non-blocking and raises :class:`LockBusy` on contention. The
    lock file records the holder process and acquisition time while held.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[bytes] | None = None

    def __enter__(self) -> "UpdateLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "ab+")
        try:
            self._acquire(self._fh)
        except LockBusy:
            self._fh.close()
            self._fh = None
            raise
        # Holder metadata supports stale-lock diagnosis without weakening the lock.
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"{os.getpid()}\n{time.time()}\n".encode())
        self._fh.flush()
        return self

    def __exit__(self, *exc) -> None:
        if self._fh is not None:
            try:
                self._release(self._fh)
            finally:
                self._fh.close()
                self._fh = None
                try:
                    self.path.unlink()
                except OSError:
                    pass

    @staticmethod
    def _acquire(fh: IO[bytes]) -> None:
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as e:
                raise LockBusy(str(e)) from e
        else:
            import fcntl

            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                raise LockBusy(str(e)) from e

    @staticmethod
    def _release(fh: IO[bytes]) -> None:
        if sys.platform == "win32":
            import msvcrt

            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl

            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def stale_age(path: Path) -> float | None:
    """Return the lock file's age in seconds, or ``None`` if unavailable."""
    try:
        st = path.stat()
    except OSError:
        return None
    return max(0.0, time.time() - st.st_mtime)


def is_stale(path: Path, threshold: float = STALE_LOCK_SECONDS) -> bool:
    """Return whether an existing lock file exceeds the stale threshold."""
    age = stale_age(path)
    return age is not None and age > threshold


def force_release(path: Path) -> None:
    """Best-effort remove a stale lock file and log the override."""
    log = get_logger()
    try:
        path.unlink()
        log.warning("launcher: force-released stale update lock at %s", path)
    except OSError as e:
        log.warning("launcher: could not force-release lock %s: %s", path, e)


__all__ = [
    "LockBusy",
    "STALE_LOCK_SECONDS",
    "UpdateLock",
    "stale_age",
    "is_stale",
    "force_release",
]
