"""Atomic, cross-process-locked persistence primitives for ``drive-settings.yaml``.

Extracted from :mod:`drive_settings` (which sits at the file-size cap) so the
save critical section is one cohesive unit — the same reason ``session`` split
:mod:`session.store_lock` out of ``store``. Every writer of the canonical
settings file goes through :func:`_atomic_write` while holding both
:data:`_SAVE_LOCK` (in-process) and the OS lock from :func:`_acquire_save_lock`
(cross-process), so no two writers can pass the optimistic-revision compare and
clobber each other (design §8.4, R1-28).
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

# Serializes the read-compare-write critical section so two in-process writers
# (e.g. two admin panels on one API server) cannot both pass the optimistic
# revision check and clobber each other. The process-local lock is the fast
# in-process gate; the OS file lock extends the same exclusion across processes.
_SAVE_LOCK = threading.Lock()

# Bounded blocking acquire for the cross-process settings lock. ``FileLock`` is
# non-blocking, so poll-retry until the peer writer releases; the ceiling stops a
# wedged (alive-but-stuck) holder from hanging an API worker forever. A crashed
# holder frees the OS lock automatically, so this ceiling is only a safety valve.
_SAVE_LOCK_TIMEOUT_S = 30.0
_SAVE_LOCK_POLL_S = 0.02


def _save_lock_path(path: Path) -> Path:
    """Sidecar lock file guarding cross-process writes to ``path`` (R1-28)."""
    return path.with_name(path.name + ".lock")


def _acquire_save_lock(path: Path) -> FileLock:
    """Block until the cross-process settings write lock is ours (R1-28).

    Serializes the read-compare-write section across processes so two server
    processes cannot both read the same revision, both pass the optimistic
    compare, and both replace the file (a lost update). Bounded; raises a
    conflict error rather than hanging if a live holder never releases."""
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
    """Open ``directory`` read-only for a durability fsync, or ``None`` on Windows.

    Opening the directory fd BEFORE the atomic replace means a directory that
    cannot be opened for fsync surfaces its error and aborts the write before the
    rename commits, so the previous settings file survives. Windows exposes no
    directory-fsync API, so there is nothing to open and rename-durability rides
    the NTFS journal instead (design §8.4, R1-28)."""
    if sys.platform == "win32":
        return None
    return os.open(str(directory), os.O_RDONLY)


def _fsync_dir_fd(fd: int | None) -> bool:
    """Fsync the open directory fd so the preceding rename is durable (POSIX).

    Returns whether the directory-entry durability barrier was actually
    established. A genuine fsync failure propagates so ``_atomic_write`` cannot
    claim a barrier it never performed. ``EINVAL`` (filesystems that reject a
    directory fsync) cannot be enforced, so it is best-effort: logged at WARNING
    and reported as ``False`` rather than raised. ``None`` (Windows, no
    directory-fsync API) also reports ``False`` (design §8.4, R1-28)."""
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
    """Write ``data`` to ``path`` via a unique same-dir temp + atomic replace.

    File *contents* are crash-durable, unconditionally: a per-write unique temp
    file (never a shared fixed name) is fsync'd, then atomically ``os.replace``-d
    over ``path``; a real content-fsync or replace failure raises and the temp is
    cleaned up, so the previous file survives. Directory-entry durability is
    best-effort: the containing directory is opened for its fsync BEFORE the
    replace (so an unopenable directory aborts pre-commit) and fsync'd after,
    but a filesystem that rejects that fsync with ``EINVAL`` cannot provide the
    barrier. Returns whether the directory-entry barrier was established —
    ``False`` on Windows (no directory-fsync API) or on an ``EINVAL`` filesystem
    (R1-28)."""
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
