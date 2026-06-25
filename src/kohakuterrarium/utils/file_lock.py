"""Cross-platform exclusive advisory file lock.

POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking``. Both are
exclusive and non-blocking — :meth:`FileLock.acquire` raises
:class:`FileLockBusy` immediately when another process (or another open
handle in this process) already holds the lock, rather than waiting.

The OS releases the lock automatically when the holding process exits or
crashes, so a lock left behind by a killed process does not wedge the
next opener — this is the key advantage over a status flag written into
a data file.

Byte-0 is reserved as the locked *anchor*; the holder pid is written
starting at byte 1. This matters on Windows, where ``msvcrt.locking``
takes a **mandatory** byte-range lock: a process holding byte 0 would
make byte 0 unreadable by any other handle. Keeping the pid out of the
locked byte lets :meth:`read_holder_pid` read it (by seeking past byte 0)
from a contending process for diagnostics. POSIX ``flock`` is whole-file
+ advisory, so the same layout works there unchanged.

The platform-specific ``fcntl`` / ``msvcrt`` imports live inside the
acquire/release methods on purpose: only one of the two modules exists
on any given OS, so importing both at module top would fail.
"""

import os
import sys
import time
from pathlib import Path
from typing import IO

# Byte 0 is the locked anchor; the pid line lives at this offset so the
# (Windows-mandatory) lock never overlaps the pid payload.
_PID_OFFSET = 1


class FileLockBusy(RuntimeError):
    """Raised when the lock is already held by another fd/process."""

    def __init__(self, message: str, holder_pid: int | None = None) -> None:
        super().__init__(message)
        self.holder_pid = holder_pid


class FileLock:
    """An exclusive advisory lock around a sidecar lock file.

    Unlike a ``with`` block, the lock is meant to be held for the
    lifetime of an owning object: call :meth:`acquire` once and
    :meth:`release` on teardown. It is also usable as a context manager.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._fh: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def read_holder_pid(self) -> int | None:
        """Best-effort: the pid recorded in the lock file, if any.

        Reads from byte ``_PID_OFFSET`` so it never touches the locked
        anchor byte — on Windows the anchor is a mandatory lock and
        reading it from a contending process would raise ``Permission
        denied``.
        """
        try:
            with open(self.path, "rb") as fh:
                fh.seek(_PID_OFFSET)
                first = fh.readline().strip()
            return int(first)
        except (OSError, ValueError):
            return None

    def acquire(self) -> None:
        """Acquire the lock. Raise :class:`FileLockBusy` if contended."""
        if self._fh is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "ab+")
        try:
            self._acquire(fh)
        except FileLockBusy as exc:
            # Annotate with the recorded holder pid before bubbling up.
            exc.holder_pid = self.read_holder_pid()
            fh.close()
            raise
        except BaseException:
            fh.close()
            raise
        # Record holder pid + acquisition time for diagnostics. Byte 0 is
        # a one-byte anchor (the locked byte); the pid line starts at
        # byte ``_PID_OFFSET`` so it stays readable while the lock is held.
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(b"\n")  # anchor byte (offset 0)
            fh.write(f"{os.getpid()}\n{time.time()}\n".encode())
            fh.flush()
        except OSError:
            pass
        self._fh = fh

    def release(self) -> None:
        """Release the lock if held. Idempotent.

        The sidecar lock file is deliberately NOT unlinked. Unlinking
        races: between unlock and unlink another process can acquire the
        same path, and the unlink would then orphan its inode so a third
        opener creates a fresh file and locks a different inode —
        split-brain. The OS advisory lock (released on ``close``) is the
        real token; a leftover ``.lock`` sidecar is harmless.
        """
        if self._fh is None:
            return
        try:
            self._release(self._fh)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    # ── platform primitives ────────────────────────────────────────────

    @staticmethod
    def _acquire(fh: IO[bytes]) -> None:
        if sys.platform == "win32":
            import msvcrt

            # ``msvcrt.locking`` locks ``nbytes`` from the CURRENT file
            # position, so seek to 0 and lock exactly the anchor byte.
            # Seeking first also avoids a stale, non-empty ``.lock`` left
            # by a crash causing a lock at EOF (a non-zero offset).
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as e:
                raise FileLockBusy(str(e)) from e
        else:
            import fcntl

            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                raise FileLockBusy(str(e)) from e

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


__all__ = ["FileLock", "FileLockBusy"]
