"""Unit tests for :mod:`kohakuterrarium.utils.file_guard`.

Every branch of the three guards plus the binary-detection helper
must be exercised — these are security-relevant primitives used by
every file-touching tool.
"""

import os
import time
from pathlib import Path

import pytest

from kohakuterrarium.utils.file_guard import (
    FileReadRecord,
    FileReadState,
    PathBoundaryGuard,
    check_read_before_write,
    is_binary_file,
)

# ── FileReadState ─────────────────────────────────────────────────────


class TestFileReadState:
    def test_get_unknown_returns_none(self):
        state = FileReadState()
        assert state.get("/anywhere/missing.txt") is None

    def test_record_and_get_roundtrip(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        st = FileReadState()
        st.record_read(str(f), mtime_ns=1234, partial=False, timestamp=42.0)
        rec = st.get(str(f))
        assert rec is not None
        assert rec.mtime_ns == 1234
        assert rec.partial is False
        assert rec.timestamp == 42.0
        # ``record.path`` is resolved-absolute, not the input string.
        assert Path(rec.path) == f.resolve()

    def test_path_is_resolved_to_canonical_form(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        st = FileReadState()
        # Record via one form, look up via another — both must resolve
        # to the same canonical path.
        st.record_read(str(f), mtime_ns=99, partial=False, timestamp=1.0)
        also = tmp_path / "." / "a.txt"
        assert st.get(str(also)) is not None
        assert st.get(str(also)).mtime_ns == 99

    def test_clear_resets_records(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        st = FileReadState()
        st.record_read(str(f), mtime_ns=1, partial=False, timestamp=1.0)
        st.clear()
        assert st.get(str(f)) is None

    def test_partial_flag_is_preserved(self, tmp_path):
        f = tmp_path / "p.txt"
        f.write_text("body")
        st = FileReadState()
        st.record_read(str(f), mtime_ns=1, partial=True, timestamp=1.0)
        assert st.get(str(f)).partial is True


# ── check_read_before_write ──────────────────────────────────────────


class TestCheckReadBeforeWrite:
    def test_new_file_always_allowed(self, tmp_path):
        # File doesn't exist on disk → ``None`` (allowed) regardless of
        # whether the read state has it.
        path = tmp_path / "never_existed.txt"
        assert check_read_before_write(None, str(path)) is None
        st = FileReadState()
        assert check_read_before_write(st, str(path)) is None

    def test_existing_file_with_no_state_blocked(self, tmp_path):
        f = tmp_path / "exists.txt"
        f.write_text("body")
        err = check_read_before_write(None, str(f))
        assert err is not None
        assert "has not been read yet" in err

    def test_existing_file_unread_state_blocked(self, tmp_path):
        f = tmp_path / "exists.txt"
        f.write_text("body")
        st = FileReadState()
        err = check_read_before_write(st, str(f))
        assert err is not None
        assert "has not been read" in err

    def test_existing_file_read_and_unchanged_allowed(self, tmp_path):
        f = tmp_path / "exists.txt"
        f.write_text("body")
        st = FileReadState()
        st.record_read(
            str(f),
            mtime_ns=os.stat(f).st_mtime_ns,
            partial=False,
            timestamp=time.time(),
        )
        assert check_read_before_write(st, str(f)) is None

    def test_existing_file_read_then_modified_is_blocked(self, tmp_path):
        f = tmp_path / "exists.txt"
        f.write_text("body")
        st = FileReadState()
        st.record_read(
            str(f), mtime_ns=os.stat(f).st_mtime_ns, partial=False, timestamp=1.0
        )
        # Bump mtime by a healthy margin (mtime_ns has 100ns or 1us
        # resolution on Windows).
        new_mtime_ns = os.stat(f).st_mtime_ns + 10_000_000
        os.utime(f, ns=(new_mtime_ns, new_mtime_ns))
        err = check_read_before_write(st, str(f))
        assert err is not None
        assert "modified since last read" in err

    def test_oserror_during_stat_treated_as_allowed(self, tmp_path, monkeypatch):
        # Inject a fake ``os`` module on file_guard so ONLY the stat
        # call inside the guard's try-block raises — patching the real
        # ``os.stat`` would also break pathlib internals (Path.resolve
        # calls os.stat on Windows) and bubble before the try-block.
        from kohakuterrarium.utils import file_guard as fg

        f = tmp_path / "exists.txt"
        f.write_text("body")
        st = FileReadState()
        st.record_read(str(f), mtime_ns=1, partial=False, timestamp=1.0)

        class _OsProxy:
            sep = os.sep

            @staticmethod
            def stat(*_a, **_kw):
                raise OSError("simulated stat failure")

        monkeypatch.setattr(fg, "os", _OsProxy)
        # If stat fails, the guard returns None and lets the write
        # attempt surface the error.
        assert check_read_before_write(st, str(f)) is None


# ── PathBoundaryGuard ────────────────────────────────────────────────


class TestPathBoundaryGuard:
    def test_off_mode_never_blocks(self, tmp_path):
        guard = PathBoundaryGuard(tmp_path, mode="off")
        outside = tmp_path.parent / "something_else.txt"
        assert guard.check(str(outside)) is None

    def test_path_inside_cwd_allowed(self, tmp_path):
        guard = PathBoundaryGuard(tmp_path, mode="warn")
        inside = tmp_path / "ok.txt"
        assert guard.check(str(inside)) is None

    def test_path_equal_to_cwd_allowed(self, tmp_path):
        guard = PathBoundaryGuard(tmp_path, mode="warn")
        assert guard.check(str(tmp_path)) is None

    def test_block_mode_always_blocks_outside(self, tmp_path):
        guard = PathBoundaryGuard(tmp_path, mode="block")
        outside = tmp_path.parent / "x.txt"
        err = guard.check(str(outside))
        assert err is not None
        assert "Access denied" in err
        # Repeat — block mode never lifts.
        err2 = guard.check(str(outside))
        assert err2 is not None
        assert "Access denied" in err2

    def test_warn_mode_first_blocked_then_allowed_on_retry(self, tmp_path):
        guard = PathBoundaryGuard(tmp_path, mode="warn")
        outside = tmp_path.parent / "y.txt"
        # First attempt — warning text returned.
        first = guard.check(str(outside))
        assert first is not None
        assert "Warning" in first
        # Second attempt to the SAME path — allowed.
        assert guard.check(str(outside)) is None

    def test_warn_mode_separately_per_path(self, tmp_path):
        guard = PathBoundaryGuard(tmp_path, mode="warn")
        out_a = tmp_path.parent / "a.txt"
        out_b = tmp_path.parent / "b.txt"
        # First attempts for two different paths — both warned.
        first_a = guard.check(str(out_a))
        first_b = guard.check(str(out_b))
        assert first_a is not None
        assert first_b is not None
        # Retry of A allowed, B still warned/allowed independently.
        assert guard.check(str(out_a)) is None


# ── is_binary_file ──────────────────────────────────────────────────


class TestIsBinaryFile:
    def test_extension_only_check_for_known_binary(self, tmp_path):
        # The function returns True via extension BEFORE reading content.
        p = tmp_path / "fake.png"
        # Don't even create the file — pure extension match returns True.
        assert is_binary_file(p) is True

    def test_kohakutr_extension_recognised(self, tmp_path):
        p = tmp_path / "session.kohakutr"
        assert is_binary_file(p) is True

    def test_text_file_with_text_content_returns_false(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("hello world\nline2\n")
        assert is_binary_file(p) is False

    def test_empty_file_is_not_binary(self, tmp_path):
        p = tmp_path / "empty.dat"
        p.write_bytes(b"")
        # ``.dat`` not in known list, content empty → False.
        assert is_binary_file(p) is False

    def test_unreadable_path_returns_false(self, tmp_path):
        # Doesn't exist, no extension match → open fails → False.
        p = tmp_path / "ghost.unknown"
        assert is_binary_file(p) is False

    def test_file_with_null_bytes_is_binary(self, tmp_path):
        p = tmp_path / "weird.dat"
        p.write_bytes(b"hello\x00world")
        assert is_binary_file(p) is True

    def test_file_with_high_control_byte_ratio_is_binary(self, tmp_path):
        p = tmp_path / "ctrl.dat"
        # 100% control bytes — well over the 10% threshold.
        p.write_bytes(bytes(range(1, 8)) * 100)
        assert is_binary_file(p) is True

    def test_utf8_text_not_misclassified(self, tmp_path):
        p = tmp_path / "utf.txt"
        p.write_bytes("中文 cuộc 한국 emoji 🌸".encode("utf-8"))
        # High bytes (>=0x80) are explicitly excluded from the
        # control-byte count — UTF-8 text MUST NOT be flagged.
        assert is_binary_file(p) is False

    def test_extension_match_takes_precedence_over_text_content(self, tmp_path):
        # Even if the file is actually text, the extension wins.
        p = tmp_path / "image.png"
        p.write_text("not really a png")
        assert is_binary_file(p) is True

    def test_accepts_pathlib_path(self, tmp_path):
        p = tmp_path / "x.txt"
        p.write_text("ok")
        # API explicitly accepts ``str | Path``.
        assert is_binary_file(p) is False
        assert is_binary_file(str(p)) is False


# ── FileReadRecord dataclass shape ───────────────────────────────────


def test_filereadrecord_has_slots_and_fields():
    r = FileReadRecord(path="/x", mtime_ns=1, partial=False, timestamp=2.0)
    assert r.path == "/x"
    assert r.mtime_ns == 1
    assert r.partial is False
    assert r.timestamp == 2.0
    # slots=True → assigning an unknown attribute raises.
    with pytest.raises(AttributeError):
        r.extra = "nope"
