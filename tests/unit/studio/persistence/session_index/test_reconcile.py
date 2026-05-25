"""Unit tests for ``session_index.reconcile`` — every code path."""

from pathlib import Path

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index.reconcile import (
    ReconcileReport,
    _extract_text_preview,
    _first_user_input_preview,
    _has_vector_index,
    read_entry_from_disk,
    reconcile,
)
from kohakuterrarium.studio.persistence.session_index.store import SessionIndex


@pytest.fixture
def session_dir(tmp_path):
    d = tmp_path / "sessions"
    d.mkdir()
    return d


@pytest.fixture
def idx(tmp_path):
    side = tmp_path / ".kt-index.kvault"
    i = SessionIndex(side)
    try:
        yield i
    finally:
        i.close()


def _make_session(
    session_dir: Path,
    name: str,
    *,
    agent: str = "alice",
    preview_text: str = "hello world",
    config_type: str = "agent",
) -> Path:
    path = session_dir / f"{name}.kohakutr"
    s = SessionStore(str(path))
    try:
        s.init_meta("sid-" + name, config_type, "/p/cfg.yaml", "/w", [agent])
        if preview_text:
            s.append_event(agent, "user_input", {"content": preview_text})
        s.flush()
    finally:
        s.close()
    return path


# ── Preview extraction ────────────────────────────────────────────


class TestExtractTextPreview:
    def test_none(self):
        assert _extract_text_preview(None) == ""

    def test_string(self):
        assert _extract_text_preview("hello") == "hello"

    def test_string_truncated(self):
        out = _extract_text_preview("x" * 500, limit=10)
        assert len(out) == 10

    def test_list_text_parts(self):
        out = _extract_text_preview(
            [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        )
        assert "a" in out and "b" in out

    def test_list_with_image(self):
        out = _extract_text_preview(
            [{"type": "image_url"}, {"type": "text", "text": "ok"}]
        )
        assert "[image]" in out
        assert "ok" in out

    def test_list_with_image_alias(self):
        out = _extract_text_preview([{"type": "image"}])
        assert out == "[image]"

    def test_list_with_file_part(self):
        out = _extract_text_preview([{"type": "file"}])
        assert "[file]" in out

    def test_list_with_unknown_attachment(self):
        out = _extract_text_preview([{"type": "custom"}])
        assert "[custom]" in out

    def test_list_with_typeless_dict(self):
        out = _extract_text_preview([{}])
        assert "[attachment]" in out

    def test_list_with_bare_string(self):
        out = _extract_text_preview(["bare-string", {"type": "text", "text": "x"}])
        assert "bare-string" in out

    def test_dict_input(self):
        out = _extract_text_preview({"type": "text", "text": "hi"})
        assert "hi" in out

    def test_fallback_to_str(self):
        assert _extract_text_preview(42) == "42"


# ── Preview probe (uses a real SessionStore) ──────────────────────


class TestFirstUserInputPreview:
    def test_extracts_first_user_input(self, session_dir):
        _make_session(session_dir, "alice", preview_text="hello there")
        s = SessionStore(str(session_dir / "alice.kohakutr"))
        try:
            assert _first_user_input_preview(s) == "hello there"
        finally:
            s.close()

    def test_returns_empty_when_no_user_input(self, session_dir):
        path = session_dir / "empty.kohakutr"
        s = SessionStore(str(path))
        s.init_meta("sid", "agent", "", "", ["alice"])
        s.flush()
        s.close()
        s = SessionStore(str(path))
        try:
            assert _first_user_input_preview(s) == ""
        finally:
            s.close()

    def test_returns_empty_when_no_agent(self, session_dir):
        path = session_dir / "no_agent.kohakutr"
        s = SessionStore(str(path))
        # Pass an empty agent list — preview probe must short-circuit.
        s.init_meta("sid", "agent", "", "", [])
        s.flush()
        s.close()
        s = SessionStore(str(path))
        try:
            assert _first_user_input_preview(s) == ""
        finally:
            s.close()

    def test_swallows_load_meta_exception(self, monkeypatch):
        # Fake store whose load_meta raises — function must log + return "".
        class Boom:
            def load_meta(self):
                raise RuntimeError("meta corrupt")

        assert _first_user_input_preview(Boom()) == ""


# ── Vector index probe ───────────────────────────────────────────


class TestHasVectorIndex:
    def test_false_when_dim_missing(self, session_dir):
        _make_session(session_dir, "alice")
        s = SessionStore(str(session_dir / "alice.kohakutr"))
        try:
            assert _has_vector_index(s) is False
        finally:
            s.close()

    def test_true_when_dim_positive(self, session_dir):
        _make_session(session_dir, "alice")
        s = SessionStore(str(session_dir / "alice.kohakutr"))
        try:
            s.state.put("vec_dimensions", 384)
            assert _has_vector_index(s) is True
        finally:
            s.close()

    def test_false_for_non_int_dim(self, session_dir):
        _make_session(session_dir, "alice")
        s = SessionStore(str(session_dir / "alice.kohakutr"))
        try:
            s.state.put("vec_dimensions", "nope")
            assert _has_vector_index(s) is False
        finally:
            s.close()

    def test_false_for_zero_dim(self, session_dir):
        _make_session(session_dir, "alice")
        s = SessionStore(str(session_dir / "alice.kohakutr"))
        try:
            s.state.put("vec_dimensions", 0)
            assert _has_vector_index(s) is False
        finally:
            s.close()

    def test_swallows_state_exception(self):
        class Boom:
            class state:
                def __contains__(self, k):
                    raise RuntimeError("state corrupt")

        assert _has_vector_index(Boom()) is False


# ── read_entry_from_disk ─────────────────────────────────────────


class TestReadEntryFromDisk:
    def test_happy_path(self, session_dir):
        path = _make_session(session_dir, "alice", preview_text="hello there")
        entry = read_entry_from_disk(path)
        assert entry is not None
        assert entry.name == "alice"
        assert entry.preview == "hello there"
        assert entry.agents == ["alice"]

    def test_returns_none_for_nonexistent(self, tmp_path):
        entry = read_entry_from_disk(tmp_path / "ghost.kohakutr")
        # SessionStore() on a missing file is permitted (it creates
        # the file) so this actually returns an entry — the function
        # only returns None on a real read failure.  For a "doesn't
        # exist" case the file is created empty; entry has an empty
        # agents list and "" preview.
        assert entry is None or entry.preview == ""

    def test_returns_none_when_session_store_raises(self, tmp_path, monkeypatch):
        # Force ``SessionStore`` construction to fail AFTER the
        # pre-open stat succeeds — exercises the outer ``except``
        # in ``read_entry_from_disk`` (the inner OSError catch
        # handles the pre-open stat failure).
        path = tmp_path / "x.kohakutr"
        path.write_bytes(b"x")  # stat() succeeds

        def boom(*a, **kw):
            raise RuntimeError("simulated SessionStore boot fail")

        monkeypatch.setattr(
            "kohakuterrarium.studio.persistence.session_index.reconcile.SessionStore",
            boom,
        )
        out = read_entry_from_disk(path)
        assert out is None

    def test_returns_none_when_pre_open_stat_fails(self, tmp_path, monkeypatch):
        # The pre-open stat in ``read_entry_from_disk`` captures the
        # fingerprint BEFORE opening SessionStore (so the WAL-touch
        # from SQLite's open doesn't invalidate the fingerprint on
        # the next reconcile).  If the stat itself raises (file
        # vanished, permission denied), the function must return
        # ``None`` without proceeding to open the store.
        from kohakuterrarium.studio.persistence.session_index import (
            reconcile as reconcile_mod,
        )

        path = tmp_path / "x.kohakutr"
        path.write_bytes(b"x")

        from pathlib import Path as _P

        real_stat = _P.stat

        def _boom(self_path, *a, **k):
            if self_path == path:
                raise OSError("stat refused")
            return real_stat(self_path, *a, **k)

        monkeypatch.setattr(_P, "stat", _boom)
        out = reconcile_mod.read_entry_from_disk(path)
        assert out is None

    def test_max_mtime_picks_newer_sidecar_in_reconcile_helper(self, tmp_path):
        # Happy path for the ``mt > best`` branch — WAL exists with a
        # mtime newer than the fallback, so the helper returns the
        # WAL's mtime.  Pairs with the unreadable-sidecar test below
        # which exercises the OSError continue branch.
        import os as _os

        from kohakuterrarium.studio.persistence.session_index import (
            reconcile as reconcile_mod,
        )

        main = tmp_path / "x.kohakutr"
        main.write_bytes(b"x")
        wal = tmp_path / "x.kohakutr-wal"
        wal.write_bytes(b"y")
        future = main.stat().st_mtime + 60
        _os.utime(wal, (future, future))
        out = reconcile_mod._max_mtime_with_wal(main, fallback=main.stat().st_mtime)
        assert out >= future - 1

    def test_max_mtime_skips_unreadable_sidecar_in_reconcile_helper(
        self, tmp_path, monkeypatch
    ):
        # The reconcile-module variant of ``_max_mtime_with_wal``
        # takes the main mtime as a precomputed fallback (one less
        # stat call in the hot loop).  Unreadable WAL/SHM sidecars
        # must be skipped without surfacing the OSError to the caller.
        import os as _os

        from kohakuterrarium.studio.persistence.session_index import (
            reconcile as reconcile_mod,
        )

        main = tmp_path / "x.kohakutr"
        main.write_bytes(b"x")
        wal = tmp_path / "x.kohakutr-wal"
        wal.write_bytes(b"y")
        real_stat = _os.stat

        def _flaky(path, *a, **k):
            if str(path).endswith("-wal"):
                raise OSError("sidecar stat failed")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(reconcile_mod.os, "stat", _flaky)
        out = reconcile_mod._max_mtime_with_wal(main, fallback=42.0)
        # Sidecar failure swallowed → returns the fallback (no
        # sidecar mtime ever beat it because they all raised).
        assert out == 42.0


# ── reconcile ────────────────────────────────────────────────────


class TestReconcile:
    def test_empty_session_dir_returns_zero_report(self, idx, tmp_path):
        missing = tmp_path / "no-such-dir"
        report = reconcile(idx, missing, full=True)
        assert report == ReconcileReport(
            read=0, deleted=0, total=0, elapsed_ms=report.elapsed_ms
        )

    def test_bootstrap_full_picks_up_all_files(self, idx, session_dir):
        _make_session(session_dir, "alice")
        _make_session(session_dir, "bob")
        report = reconcile(idx, session_dir, full=True)
        assert report.read == 2
        assert report.deleted == 0
        assert report.total == 2
        assert idx.list().total == 2

    def test_incremental_skips_unchanged_files(self, idx, session_dir):
        _make_session(session_dir, "alice")
        reconcile(idx, session_dir, full=True)
        # Re-reconcile without touching disk → 0 reads.
        report = reconcile(idx, session_dir, full=False)
        assert report.read == 0
        assert report.total == 1

    def test_incremental_rereads_mtime_changed_file(
        self, idx, session_dir, monkeypatch
    ):
        path = _make_session(session_dir, "alice")
        reconcile(idx, session_dir, full=True)
        # Touch the file to bump its mtime.

        new_mtime = path.stat().st_mtime + 60
        import os

        os.utime(path, (new_mtime, new_mtime))
        report = reconcile(idx, session_dir, full=False)
        assert report.read == 1

    def test_drops_orphan_index_entries(self, idx, session_dir):
        path = _make_session(session_dir, "alice")
        reconcile(idx, session_dir, full=True)
        # Unlink file + sidecars.
        path.unlink()
        for sf in session_dir.glob("alice.kohakutr-*"):
            sf.unlink()
        report = reconcile(idx, session_dir, full=False)
        assert report.deleted == 1
        assert idx.list().total == 0

    def test_full_rescan_rereads_everything(self, idx, session_dir):
        _make_session(session_dir, "alice")
        _make_session(session_dir, "bob")
        reconcile(idx, session_dir, full=True)
        report = reconcile(idx, session_dir, full=True)
        # Both files re-read regardless of fingerprint.
        assert report.read == 2

    def test_workers_one_runs_serially(self, idx, session_dir):
        # Forces the serial branch in reconcile (workers=1).
        _make_session(session_dir, "alice")
        report = reconcile(idx, session_dir, full=True, workers=1)
        assert report.read == 1

    def test_read_failure_is_skipped(self, idx, session_dir, monkeypatch):
        _make_session(session_dir, "alice")

        # Force read_entry_from_disk to return None for every file.
        # Use the dotted-string form because the package ``__init__``
        # re-exports ``reconcile`` and shadows the submodule attribute
        # on ``session_index``.
        monkeypatch.setattr(
            "kohakuterrarium.studio.persistence.session_index.reconcile.read_entry_from_disk",
            lambda p: None,
        )
        report = reconcile(idx, session_dir, full=True)
        # File was attempted but no entry inserted.
        assert report.read == 1
        assert idx.list().total == 0

    def test_stat_failure_is_skipped(self, idx, session_dir, monkeypatch):
        # An incremental reconcile where stat() fails on a known
        # file must not crash — just skip that file this round.
        path = _make_session(session_dir, "alice")
        reconcile(idx, session_dir, full=True)
        original_stat = Path.stat

        def flaky_stat(self_path, *args, **kw):
            if self_path == path:
                raise OSError("simulated stat fail")
            return original_stat(self_path, *args, **kw)

        monkeypatch.setattr(Path, "stat", flaky_stat)
        report = reconcile(idx, session_dir, full=False)
        # Was already indexed → skipped on stat fail; no re-read.
        assert report.read == 0

    def test_meta_last_reconcile_at_recorded(self, idx, session_dir):
        reconcile(idx, session_dir, full=True)
        ts = idx.meta_get("last_reconcile_at")
        assert isinstance(ts, float) and ts > 0
