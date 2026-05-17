"""Cross-platform exclusive file lock for the update flow.

POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking``.  Both are
exclusive, non-blocking by default; the wrapper exposes a
``UpdateLock`` context manager + a stale-lock detector so a crashed
prior update doesn't permanently wedge the wrapper.
"""

import os
import sys
import time
from pathlib import Path
from typing import IO

from kohakuterrarium.launcher.log import get_logger

STALE_LOCK_SECONDS = 10 * 60  # 10 minutes — see design.md §15


class LockBusy(RuntimeError):
    """Raised when another process holds the update lock."""


class UpdateLock:
    """Context manager around a flock on ``runtime/.update.lock``.

    ``with UpdateLock(path):`` acquires (raises :class:`LockBusy` if
    contended).  ``stale_age()`` returns the age in seconds of an
    existing lock file so the caller can decide whether to prompt the
    user to override.
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
        # Record the holder + start time for stale-lock detection.
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
    """Return age (seconds) of the lock file if it exists; ``None`` otherwise.

    Used by the UI to decide whether to prompt the user to override a
    suspected-crashed prior update.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return max(0.0, time.time() - st.st_mtime)


def is_stale(path: Path, threshold: float = STALE_LOCK_SECONDS) -> bool:
    age = stale_age(path)
    return age is not None and age > threshold


def force_release(path: Path) -> None:
    """Best-effort: delete the lock file.

    Used after the user confirms "override stale lock".  Logs a warning
    so the action is auditable.
    """
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
