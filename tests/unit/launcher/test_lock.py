"""Tests for the launcher's cross-platform update lock."""

import sys
from types import SimpleNamespace

import pytest

from kohakuterrarium.launcher import _lock


class TestUpdateLock:
    def test_windows_acquire_locks_byte_zero(self, monkeypatch, tmp_path) -> None:
        positions = []
        fake_msvcrt = SimpleNamespace(
            LK_NBLCK=1,
            locking=lambda _fd, _mode, _count: positions.append(fh.tell()),
        )
        monkeypatch.setattr(_lock.sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

        path = tmp_path / ".update.lock"
        path.write_bytes(b"stale holder metadata")
        with open(path, "ab+") as fh:
            _lock.UpdateLock._acquire(fh)

        assert positions == [0]

    def test_second_holder_is_rejected_and_sidecar_is_reused(self, tmp_path) -> None:
        path = tmp_path / ".update.lock"
        first = _lock.UpdateLock(path)
        second = _lock.UpdateLock(path)
        first.__enter__()
        try:
            with pytest.raises(_lock.LockBusy):
                second.__enter__()
        finally:
            second.__exit__(None, None, None)
            first.__exit__(None, None, None)

        assert path.exists()

        with _lock.UpdateLock(path):
            pass
        assert path.exists()
