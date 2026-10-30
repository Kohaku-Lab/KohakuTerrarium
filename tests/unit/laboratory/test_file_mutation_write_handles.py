"""Write-handle close proof and handle-free replace/unlink occupancy."""

import pytest

from kohakuterrarium.laboratory.adapters.terrarium import file_mutations as mut
from kohakuterrarium.laboratory.adapters.terrarium.file_mutations import (
    replace_enrolled,
    unlink_enrolled,
    write_bytes_enrolled,
)
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_LEGACY_RELEASE_UNCERTAIN,
    try_fast,
)
from tests.unit.laboratory.file_mutation_enrollment_helpers import (
    assert_idle,
    plain_file,
)


class TestWriteCloseProof:
    def test_raising_open_without_returned_handle_stays_uncertain(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "unknown-open.bin"
        held = []
        real_open = open
        primary = OSError("factory failed after opening")

        def opener(*args):
            held.append(real_open(*args))
            raise primary

        monkeypatch.setattr(mut, "open", opener, raising=False)
        try:
            with pytest.raises(OSError) as caught:
                write_bytes_enrolled(path, b"content")
            assert caught.value is primary
            assert (
                try_fast(path, eligible=True).reason == REASON_LEGACY_RELEASE_UNCERTAIN
            )
        finally:
            for handle in held:
                handle.close()

    def test_write_error_with_known_close_releases(self, tmp_path, monkeypatch):
        path = tmp_path / "fail-write.bin"

        class _FailWrite:
            def write(self, data):
                raise OSError("disk full")

            def close(self):
                return None

        def fake_open(target, mode="r", *args, **kwargs):
            if mode == "wb":
                return _FailWrite()
            return open(target, mode, *args, **kwargs)

        monkeypatch.setattr(mut, "open", fake_open, raising=False)
        with pytest.raises(OSError, match="disk full"):
            write_bytes_enrolled(path, b"data")
        snap = GATE.snapshot(path)
        assert snap.legacy_active == 0
        assert snap.legacy_uncertain is False
        assert_idle(path)

    def test_close_failure_marks_uncertain_and_preserves_close_cause(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "fail-close.bin"

        class _FailClose:
            def write(self, data):
                return len(data)

            def close(self):
                raise OSError("flush failed")

        def fake_open(target, mode="r", *args, **kwargs):
            if mode == "wb":
                return _FailClose()
            return open(target, mode, *args, **kwargs)

        monkeypatch.setattr(mut, "open", fake_open, raising=False)
        with pytest.raises(OSError, match="flush failed"):
            write_bytes_enrolled(path, b"data")
        snap = GATE.snapshot(path)
        assert snap.legacy_uncertain is True
        denied = try_fast(path, eligible=True)
        assert denied.reason == REASON_LEGACY_RELEASE_UNCERTAIN

    def test_write_then_close_failure_preserves_original_write_exception(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "fail-both.bin"

        class _FailBoth:
            def write(self, data):
                raise OSError("write boom")

            def close(self):
                raise OSError("close boom")

        def fake_open(target, mode="r", *args, **kwargs):
            if mode == "wb":
                return _FailBoth()
            return open(target, mode, *args, **kwargs)

        monkeypatch.setattr(mut, "open", fake_open, raising=False)
        with pytest.raises(OSError, match="write boom") as caught:
            write_bytes_enrolled(path, b"data")
        assert isinstance(caught.value.__cause__, OSError)
        assert "close boom" in str(caught.value.__cause__)
        assert GATE.snapshot(path).legacy_uncertain is True


class TestUnknownOpenFactory:
    def test_no_handle_after_actual_open_marks_uncertain(self, tmp_path, monkeypatch):
        path = tmp_path / "leaked.bin"
        leaked: list[object] = []
        real_open = open

        def fake_open(target, mode="r", *args, **kwargs):
            if mode == "wb":
                handle = real_open(target, mode, *args, **kwargs)
                leaked.append(handle)
                return None
            return real_open(target, mode, *args, **kwargs)

        monkeypatch.setattr(mut, "open", fake_open, raising=False)
        try:
            with pytest.raises(AttributeError):
                write_bytes_enrolled(path, b"data")
            snap = GATE.snapshot(path)
            assert snap.legacy_uncertain is True
            denied = try_fast(path, eligible=True)
            assert denied.reason == REASON_LEGACY_RELEASE_UNCERTAIN
            assert leaked
        finally:
            for handle in leaked:
                handle.close()


class TestHandleFreeRelease:
    def test_replace_error_still_releases(self, tmp_path, monkeypatch):
        src = plain_file(tmp_path, "src.bin", b"new")
        dst = plain_file(tmp_path, "dst.bin", b"old")

        def boom_replace(*args, **kwargs):
            raise PermissionError(13, "locked")

        monkeypatch.setattr(mut.os, "replace", boom_replace)
        with pytest.raises(PermissionError):
            replace_enrolled(src, dst)
        assert src.exists() and dst.exists()
        assert_idle(src)
        assert_idle(dst)

    def test_unlink_error_still_releases(self, tmp_path, monkeypatch):
        path = plain_file(tmp_path, "locked.bin")

        def boom_unlink(*args, **kwargs):
            raise PermissionError(13, "locked")

        monkeypatch.setattr(mut.os, "unlink", boom_unlink)
        with pytest.raises(PermissionError):
            unlink_enrolled(path)
        assert path.exists()
        assert_idle(path)
