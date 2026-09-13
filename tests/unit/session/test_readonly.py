"""Unit tests for :mod:`kohakuterrarium.session.readonly`."""

from pathlib import Path

import pytest

from kohakuterrarium.session.readonly import read_session_meta
from kohakuterrarium.session.store import SessionStore


def _closed_session(path: Path) -> Path:
    store = SessionStore(str(path), writer_lock=True)
    try:
        store.init_meta("sess", "agent", "/p", "/w", ["alice"])
    finally:
        store.close()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    return path


class TestReadSessionMeta:
    @pytest.mark.parametrize("as_uri", [False, True])
    def test_read_preserves_source_and_sidecars(self, tmp_path, monkeypatch, as_uri):
        monkeypatch.chdir(tmp_path)
        session = _closed_session(tmp_path / "s %20 #.kohakutr")
        before = {
            p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()
        }

        meta = read_session_meta(session.as_uri() if as_uri else session)

        assert meta["session_id"] == "sess"
        assert {
            p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()
        } == before

    def test_wal_free_read_does_not_mkdir_cwd_file_scheme(self, tmp_path, monkeypatch):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        session = tmp_path / "real" / "s.kohakutr"
        _closed_session(session)
        monkeypatch.chdir(cwd)

        meta = read_session_meta(session)

        assert meta["session_id"] == "sess"
        assert not (cwd / "file:").exists()

    def test_file_uri_reads_the_named_session(self, tmp_path, monkeypatch):
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        session = tmp_path / "real" / "s.kohakutr"
        session.parent.mkdir()
        _closed_session(session)
        monkeypatch.chdir(cwd)

        meta = read_session_meta(session.resolve().as_uri())

        assert meta["session_id"] == "sess"
        assert not (cwd / "file:").exists()

    def test_copies_wal_sidecar_when_present(self, tmp_path):
        session = tmp_path / "s.kohakutr"
        store = SessionStore(str(session), writer_lock=True)
        try:
            store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            store.append_event("alice", "text", {"content": "hello"})
            store.flush()
            assert Path(f"{session}-wal").stat().st_size > 0
            meta = read_session_meta(session)
            assert meta["session_id"] == "sess"
            assert store.load_meta()["session_id"] == "sess"
        finally:
            store.close()
