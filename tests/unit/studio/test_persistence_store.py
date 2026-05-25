"""Unit tests for :mod:`kohakuterrarium.studio.persistence.store`.

The legacy in-memory listing helpers (``build_session_index`` /
``get_session_index`` / ``session_stats`` / ``_read_session_entry`` /
``_extract_text_preview`` / ``_max_mtime``) have been removed in
favour of the sidecar at :mod:`studio.persistence.session_index`.
Those code paths are covered by the test suite under
``tests/unit/studio/persistence/session_index/``.  What remains
here is the per-session filesystem + history surface that this
module still owns: ``_session_dir`` resolution, ``disk_usage``,
path helpers, ``session_targets`` / ``session_history_payload``,
``delete_session_files``.
"""

from pathlib import Path

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import store as store_mod

# ── _session_dir ──────────────────────────────────────────────


class TestSessionDir:
    def test_resolves_to_the_sessions_subdir(self):
        out = store_mod._session_dir()
        # Sessions live under the '<config home>/sessions' directory.
        assert out.name == "sessions"


# ── shared helper ────────────────────────────────────────────


def _make_session(tmp_path, name="alice", agent="alice", meta_extra=None):
    path = tmp_path / f"{name}.kohakutr"
    s = SessionStore(str(path))
    try:
        s.init_meta("sess", "agent", "/p", "/w", [agent])
        if meta_extra:
            for k, v in meta_extra.items():
                s.meta[k] = v
        s.append_event(agent, "user_input", {"content": "hello"})
        s.flush()
    finally:
        s.close()
    return path


# ── disk_usage ────────────────────────────────────────────────


class TestDiskUsage:
    def test_missing_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path / "no-such")
        out = store_mod.disk_usage()
        assert out["count"] == 0
        assert out["total_bytes"] == 0

    def test_with_sessions(self, tmp_path, monkeypatch):
        path = _make_session(tmp_path)
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        out = store_mod.disk_usage()
        # Exactly one canonical session was written; total_bytes counts
        # the session file plus its sidecars (>= the main file size).
        assert out["count"] == 1
        assert out["total_bytes"] >= path.stat().st_size > 0


# ── resolve_session_path_default / all_versions_for_session_default ─


class TestPathHelpers:
    def test_resolve(self, tmp_path, monkeypatch):
        path = _make_session(tmp_path)
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        out = store_mod.resolve_session_path_default("alice")
        assert out == path

    def test_all_versions(self, tmp_path, monkeypatch):
        path = _make_session(tmp_path)
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        out = store_mod.all_versions_for_session_default("alice")
        # The single saved session file is the only version.
        assert out == [path]


# ── session_targets ────────────────────────────────────────────


class TestSessionTargets:
    def test_uses_meta_agents(self, tmp_path):
        path = _make_session(tmp_path)
        s = SessionStore(str(path))
        try:
            meta = s.load_meta()
            out = store_mod.session_targets(s, meta)
            assert "alice" in out
        finally:
            s.close()

    def test_meta_with_channels(self, tmp_path):
        path = _make_session(
            tmp_path,
            meta_extra={"terrarium_channels": [{"name": "chat"}, {"name": "ops"}]},
        )
        s = SessionStore(str(path))
        try:
            meta = s.load_meta()
            out = store_mod.session_targets(s, meta)
            assert "ch:chat" in out
            assert "ch:ops" in out
        finally:
            s.close()


# ── session_history_payload ────────────────────────────────────


class TestSessionHistoryPayload:
    def test_channel_target(self, tmp_path):
        path = _make_session(tmp_path)
        s = SessionStore(str(path))
        try:
            s.save_channel_message("ch1", {"sender": "alice", "content": "hi"})
            s.flush()
            out = store_mod.session_history_payload(s, "ch:ch1")
            assert out["target"] == "ch:ch1"
            assert out["events"][0]["type"] == "channel_message"
        finally:
            s.close()

    def test_agent_target(self, tmp_path):
        path = _make_session(tmp_path)
        s = SessionStore(str(path))
        try:
            out = store_mod.session_history_payload(s, "alice")
            assert out["target"] == "alice"
            assert "events" in out
        finally:
            s.close()


# ── delete_session_files ──────────────────────────────────────


class TestDeleteSessionFiles:
    def test_no_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        out = store_mod.delete_session_files("ghost")
        assert out == []

    def test_deletes(self, tmp_path, monkeypatch):
        path = _make_session(tmp_path)
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        deleted = store_mod.delete_session_files("alice")
        assert len(deleted) >= 1
        # Path is gone.
        assert not path.exists()

    def test_deletes_wal_shm_sidecars(self, tmp_path, monkeypatch):
        # Bug #59: SQLite WAL mode writes ``-wal`` + ``-shm`` next to
        # the main file.  Pre-fix ``delete_session_files`` only
        # globbed ``*.kohakutr*``, leaving the sidecars behind to
        # waste disk and confuse re-creates of the same session name.
        path = _make_session(tmp_path)
        wal = path.with_name(path.name + "-wal")
        shm = path.with_name(path.name + "-shm")
        wal.write_bytes(b"fake wal")
        shm.write_bytes(b"fake shm")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        store_mod.delete_session_files("alice")
        assert not path.exists()
        assert not wal.exists(), "WAL sidecar leaked after delete"
        assert not shm.exists(), "SHM sidecar leaked after delete"

    def test_retries_on_transient_permission_error(self, tmp_path, monkeypatch):
        # Bug #59 (Windows-only repro in production): the close-handles
        # nudge happens via Python refcounting which can lag a few ms
        # behind ``unlink``.  ``_unlink_with_retry`` must retry rather
        # than propagate the first ``PermissionError`` straight to the
        # 409 handler.
        path = _make_session(tmp_path)
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        # Speed up the backoff so the test isn't slow.
        monkeypatch.setattr(store_mod.time, "sleep", lambda _s: None)

        real_unlink = Path.unlink
        calls = {"n": 0}

        def flaky_unlink(self_path, *args, **kw):
            # First call on the main file raises; subsequent succeed.
            if self_path == path and calls["n"] == 0:
                calls["n"] += 1
                raise PermissionError(13, "simulated Windows file lock")
            return real_unlink(self_path, *args, **kw)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)
        deleted = store_mod.delete_session_files("alice")
        assert len(deleted) >= 1
        assert not path.exists()
        assert calls["n"] == 1, "the flaky path should have been retried"
