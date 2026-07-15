"""Atomic, cross-process-locked persistence primitives for ``drive-settings.yaml``.

Every writer of the canonical settings file uses :func:`_atomic_write` while
holding both :data:`_SAVE_LOCK` for threads and the OS lock from
:func:`_acquire_save_lock` for processes. Revision validation and replacement
therefore form one exclusive critical section, preventing lost updates.
"""

import errno
import hashlib
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

from kohakuterrarium.terrarium.drive.errors import (
    DriveSettingsConflictError,
    DriveValidationError,
)
from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Serialize revision validation and replacement between threads; the sidecar OS
# lock extends the same invariant across processes.
_SAVE_LOCK = threading.Lock()

# ``FileLock`` is non-blocking, so bounded polling prevents a wedged live holder
# from stalling an API worker indefinitely. The OS releases locks after crashes.
_SAVE_LOCK_TIMEOUT_S = 30.0
_SAVE_LOCK_POLL_S = 0.02


def _save_lock_path(path: Path) -> Path:
    """Return the sidecar path that guards cross-process writes to ``path``."""
    return path.with_name(path.name + ".lock")


def _acquire_save_lock(path: Path) -> FileLock:
    """Acquire the bounded cross-process settings lock.

    The lock covers revision validation and replacement so competing processes
    cannot commit from the same stale revision. A live holder that exceeds the
    timeout produces a conflict instead of an indefinite wait.
    """
    lock = FileLock(str(_save_lock_path(path)))
    deadline = time.monotonic() + _SAVE_LOCK_TIMEOUT_S
    while True:
        try:
            lock.acquire()
            return lock
        except FileLockBusy:
            if time.monotonic() >= deadline:
                raise DriveSettingsConflictError(
                    "drive-settings.yaml is locked by another writer; retry",
                    expected_revision=None,
                    actual_revision=None,
                )
            time.sleep(_SAVE_LOCK_POLL_S)


def _open_dir_fd(directory: Path) -> int | None:
    """Open a directory descriptor for a durability fsync when supported.

    Opening before replacement makes descriptor failures pre-commit, preserving
    the previous file. Windows has no directory-fsync API and returns ``None``.
    """
    if sys.platform == "win32":
        return None
    return os.open(str(directory), os.O_RDONLY)


def _fsync_dir_fd(fd: int | None) -> bool:
    """Fsync a directory descriptor and report whether the barrier succeeded.

    Unsupported descriptors and filesystems that reject directory fsync with
    ``EINVAL`` return ``False``. Other failures propagate so callers never claim
    durability that was not established.
    """
    if fd is None:
        return False
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno != errno.EINVAL:
            raise
        logger.warning(
            "directory fsync rejected (EINVAL): directory-entry durability is "
            "best-effort on this filesystem; file contents remain crash-durable"
        )
        return False
    return True


def _check_expected_revision(
    path: Path,
    *,
    expected_revision: str | None,
    expected_exists: bool | None,
) -> None:
    """Enforce an explicit revision/existence precondition while locked.

    ``expected_exists=None`` with no revision is an unconditional write;
    ``False`` is expect-absent; ``True`` requires an existing file. A revision
    always requires an existing file and exact content-hash equality.
    """
    if expected_exists is False and expected_revision is not None:
        raise DriveValidationError(
            "expected_revision cannot be used with expected_exists=False"
        )
    actual_exists = path.is_file()
    actual_revision = (
        hashlib.sha256(path.read_bytes()).hexdigest() if actual_exists else None
    )
    mismatch = (
        expected_exists is not None and actual_exists is not expected_exists
    ) or (expected_revision is not None and actual_revision != expected_revision)
    if mismatch:
        raise DriveSettingsConflictError(
            "drive-settings.yaml changed since it was loaded; refetch and retry",
            expected_revision=expected_revision,
            actual_revision=actual_revision,
        )


def _atomic_write(path: Path, data: bytes) -> bool:
    """Atomically replace ``path`` with crash-durable file contents.

    A unique same-directory temporary file is flushed and fsynced before
    replacement. The directory is opened before commit and fsynced afterward
    when supported. Failures clean up the temporary file and preserve the prior
    destination; the return value reports directory-entry durability.
    """
    dir_barrier = False
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".kt-tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        dir_fd = _open_dir_fd(path.parent)
        try:
            os.replace(tmp, path)
            dir_barrier = _fsync_dir_fd(dir_fd)
        finally:
            if dir_fd is not None:
                os.close(dir_fd)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return dir_barrier


__all__ = [
    "_SAVE_LOCK",
    "_acquire_save_lock",
    "_atomic_write",
    "_check_expected_revision",
    "_fsync_dir_fd",
    "_open_dir_fd",
    "_save_lock_path",
]
