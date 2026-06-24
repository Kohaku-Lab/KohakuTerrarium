"""Unit tests for ``utils/file_lock.py`` — cross-process advisory lock.

Behavior-first: assert that a second acquire on the same path is refused
while the first is held (even within this process — a different open file
description conflicts), that release frees it for re-acquisition, that the
holder pid is recorded + surfaced, idempotent release, and the context
manager form.
"""

import os

import pytest

from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy


class TestAcquireRelease:
    def test_second_acquire_is_refused(self, tmp_path):
        lp = tmp_path / "x.lock"
        a = FileLock(lp)
        a.acquire()
        try:
            b = FileLock(lp)
            with pytest.raises(FileLockBusy) as exc:
                b.acquire()
            # The holder pid is recorded in the lock file and surfaced.
            assert exc.value.holder_pid == os.getpid()
            assert b.held is False
        finally:
            a.release()

    def test_release_frees_for_reacquire(self, tmp_path):
        lp = tmp_path / "x.lock"
        a = FileLock(lp)
        a.acquire()
        a.release()
        assert a.held is False
        # A fresh lock can now take it.
        b = FileLock(lp)
        b.acquire()
        assert b.held is True
        b.release()

    def test_release_is_idempotent(self, tmp_path):
        a = FileLock(tmp_path / "x.lock")
        a.acquire()
        a.release()
        a.release()  # no raise
        assert a.held is False

    def test_double_acquire_same_object_is_noop(self, tmp_path):
        a = FileLock(tmp_path / "x.lock")
        a.acquire()
        a.acquire()  # already held — no raise, still held
        assert a.held is True
        a.release()

    def test_lockfile_persists_after_release(self, tmp_path):
        # The sidecar file is intentionally NOT unlinked on release
        # (unlinking races -> split-brain). It stays and is reacquirable.
        lp = tmp_path / "x.lock"
        a = FileLock(lp)
        a.acquire()
        a.release()
        assert lp.exists()
        b = FileLock(lp)
        b.acquire()  # still acquirable on the leftover file
        b.release()


class TestContextManager:
    def test_with_block_acquires_and_releases(self, tmp_path):
        lp = tmp_path / "x.lock"
        with FileLock(lp) as held:
            assert held.held is True
            other = FileLock(lp)
            with pytest.raises(FileLockBusy):
                other.acquire()
        # released on exit -> acquirable again
        post = FileLock(lp)
        post.acquire()
        post.release()


class TestHolderPid:
    def test_read_holder_pid_after_acquire(self, tmp_path):
        a = FileLock(tmp_path / "x.lock")
        a.acquire()
        try:
            assert a.read_holder_pid() == os.getpid()
        finally:
            a.release()

    def test_read_holder_pid_missing_file(self, tmp_path):
        assert FileLock(tmp_path / "nope.lock").read_holder_pid() is None
