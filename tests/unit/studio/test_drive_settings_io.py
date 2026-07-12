"""R1-28: durability + cleanup contract of ``drive_settings_io._atomic_write``.

The atomic settings write must, in order, fsync the staged temp file, atomically
``os.replace`` it over the canonical path, then fsync the containing directory
(POSIX only — Windows exposes no directory-fsync API and relies on the NTFS
journal). Any failure before the replace commits must remove the unique temp
file and leave the previous settings file byte-for-byte intact.

``os.fsync`` / ``os.replace`` are monkeypatched to RECORD their call order and to
INJECT failures, so the sequence and the failure-cleanup are pinned directly
rather than inferred. Uses ``tmp_path`` only — no config dir, no real settings.
"""

import errno
import os
import sys

import pytest

from kohakuterrarium.studio.identity import drive_settings_io as ds_io

# Sentinel directory fd distinct from any real descriptor, so injected
# fsync/close simulation targets only the directory barrier and never the
# temp file's real I/O.
_DIR_FD = -424242


def _patch_posix_dir(monkeypatch, *, open_error=None, fsync_error=None) -> None:
    """Force the POSIX directory-fsync path and inject open/fsync failures.

    Runs deterministically on Windows too: ``sys.platform`` is forced non-win32
    (real directory fds are not openable there) and the directory ``os.open``
    returns ``_DIR_FD``, so only that sentinel's fsync/close are simulated while
    the temp file's real writes and fsync are untouched."""
    real_open = os.open
    real_fsync = os.fsync
    real_close = os.close

    def fake_open(pathname, flags, *args, **kwargs):
        if os.path.isdir(pathname):
            if open_error is not None:
                raise open_error
            return _DIR_FD
        return real_open(pathname, flags, *args, **kwargs)

    def fake_fsync(fd) -> None:
        if fd == _DIR_FD:
            if fsync_error is not None:
                raise fsync_error
            return
        real_fsync(fd)

    def fake_close(fd) -> None:
        if fd != _DIR_FD:
            real_close(fd)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "open", fake_open)
    monkeypatch.setattr(os, "fsync", fake_fsync)
    monkeypatch.setattr(os, "close", fake_close)


def _record_io(monkeypatch) -> list[str]:
    """Record fsync/replace order; replace still performs the real rename."""
    events: list[str] = []
    real_replace = os.replace

    def rec_fsync(fd: int) -> None:
        events.append("fsync")

    def rec_replace(src, dst) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", rec_fsync)
    monkeypatch.setattr(os, "replace", rec_replace)
    return events


class TestAtomicWriteSequence:
    def test_fsync_temp_then_replace_then_fsync_dir(self, tmp_path, monkeypatch):
        path = tmp_path / "drive-settings.yaml"
        events = _record_io(monkeypatch)

        ds_io._atomic_write(path, b"payload")

        assert path.read_bytes() == b"payload"
        # Temp fsync strictly precedes the atomic replace.
        assert events[0] == "fsync"
        assert events[1] == "replace"
        if sys.platform == "win32":
            # No directory-fsync API on Windows; the journal covers rename.
            assert events == ["fsync", "replace"]
        else:
            # Directory fsync follows the replace so the rename is durable.
            assert events == ["fsync", "replace", "fsync"]

    def test_no_temp_file_left_after_success(self, tmp_path, monkeypatch):
        path = tmp_path / "drive-settings.yaml"
        _record_io(monkeypatch)
        ds_io._atomic_write(path, b"payload")
        assert list(tmp_path.glob("*.kt-tmp")) == []


class TestAtomicWriteFailureCleanup:
    def test_temp_fsync_failure_removes_temp_and_preserves_previous(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "drive-settings.yaml"
        path.write_bytes(b"previous")

        def boom_fsync(fd: int) -> None:
            raise OSError("simulated fsync failure")

        # If os.replace were reached it would clobber the previous file; failing
        # at the temp fsync must abort BEFORE the replace.
        monkeypatch.setattr(os, "fsync", boom_fsync)
        monkeypatch.setattr(
            os,
            "replace",
            lambda *a: pytest.fail("replace ran after a temp fsync failure"),
        )

        with pytest.raises(OSError, match="simulated fsync failure"):
            ds_io._atomic_write(path, b"new")

        assert path.read_bytes() == b"previous"
        assert list(tmp_path.glob("*.kt-tmp")) == []

    def test_replace_failure_removes_temp_and_preserves_previous(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "drive-settings.yaml"
        path.write_bytes(b"previous")
        monkeypatch.setattr(os, "fsync", lambda fd: None)

        def boom_replace(src, dst) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr(os, "replace", boom_replace)

        with pytest.raises(OSError, match="simulated replace failure"):
            ds_io._atomic_write(path, b"new")

        assert path.read_bytes() == b"previous"
        assert list(tmp_path.glob("*.kt-tmp")) == []

    def test_first_write_replace_failure_leaves_no_file_or_temp(
        self, tmp_path, monkeypatch
    ):
        # No previous settings file: a failed first write must not leave a
        # partial canonical file OR a staged temp behind.
        path = tmp_path / "drive-settings.yaml"
        monkeypatch.setattr(os, "fsync", lambda fd: None)
        monkeypatch.setattr(
            os, "replace", lambda *a: (_ for _ in ()).throw(OSError("boom"))
        )

        with pytest.raises(OSError):
            ds_io._atomic_write(path, b"new")

        assert not path.exists()
        assert list(tmp_path.glob("*.kt-tmp")) == []


class TestAtomicWriteDirBarrier:
    """POSIX directory-fsync failures must surface, not be swallowed into a
    false success (the whole point of fsync-ing the containing dir after the
    rename). Exercised deterministically on every platform via ``_patch_posix_dir``."""

    def test_dir_open_failure_aborts_before_commit_preserves_previous(
        self, tmp_path, monkeypatch
    ):
        # The barrier cannot be established at all (directory unopenable): abort
        # BEFORE the replace so the previous file survives and no temp is left.
        path = tmp_path / "drive-settings.yaml"
        path.write_bytes(b"previous")
        _patch_posix_dir(
            monkeypatch, open_error=OSError(errno.EACCES, "dir open denied")
        )

        with pytest.raises(OSError, match="dir open denied"):
            ds_io._atomic_write(path, b"new")

        assert path.read_bytes() == b"previous"
        assert list(tmp_path.glob("*.kt-tmp")) == []

    def test_dir_fsync_failure_propagates_and_cleans_temp(self, tmp_path, monkeypatch):
        # Post-replace directory fsync failure: the write must RAISE (not report
        # a false success) and clean its temp. The replace has already committed,
        # so the on-disk content is the fully-written new bytes, never torn.
        path = tmp_path / "drive-settings.yaml"
        path.write_bytes(b"previous")
        _patch_posix_dir(
            monkeypatch, fsync_error=OSError(errno.EIO, "dir fsync failed")
        )

        with pytest.raises(OSError, match="dir fsync failed"):
            ds_io._atomic_write(path, b"new")

        assert path.read_bytes() == b"new"
        assert list(tmp_path.glob("*.kt-tmp")) == []

    def test_dir_fsync_einval_is_tolerated_but_signalled(
        self, tmp_path, monkeypatch, caplog
    ):
        # EINVAL is the legitimate carve-out (filesystems that reject a directory
        # fsync): the write still commits the file contents, but the weakened
        # directory-entry barrier must be OBSERVABLE, never a silent success. The
        # return signal reports the barrier as not established AND a WARNING is
        # logged; the contract only promises crash-durable file *contents* here.
        path = tmp_path / "drive-settings.yaml"
        _patch_posix_dir(
            monkeypatch, fsync_error=OSError(errno.EINVAL, "dir fsync unsupported")
        )

        with caplog.at_level("WARNING", logger="kohakuterrarium"):
            dir_barrier = ds_io._atomic_write(path, b"new")

        assert dir_barrier is False
        assert path.read_bytes() == b"new"
        assert list(tmp_path.glob("*.kt-tmp")) == []
        assert any(
            record.levelname == "WARNING" and "EINVAL" in record.getMessage()
            for record in caplog.records
        )
