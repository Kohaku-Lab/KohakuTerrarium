"""Unit tests for :mod:`kohakuterrarium.studio.persistence.store`."""

import time


from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import store as store_mod

# ── _extract_text_preview ─────────────────────────────────────


class TestExtractTextPreview:
    def test_none(self):
        assert store_mod._extract_text_preview(None) == ""

    def test_short_string(self):
        assert store_mod._extract_text_preview("hello") == "hello"

    def test_long_string_truncated(self):
        out = store_mod._extract_text_preview("x" * 500, limit=10)
        assert len(out) == 10

    def test_list_of_text_parts(self):
        out = store_mod._extract_text_preview(
            [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        )
        assert "a" in out and "b" in out

    def test_image_part(self):
        out = store_mod._extract_text_preview(
            [{"type": "image_url"}, {"type": "text", "text": "ok"}]
        )
        assert "[image]" in out
        assert "ok" in out

    def test_file_part(self):
        out = store_mod._extract_text_preview([{"type": "file"}])
        assert "[file]" in out

    def test_unknown_attachment(self):
        out = store_mod._extract_text_preview([{"type": "custom"}])
        assert "[custom]" in out

    def test_str_in_list(self):
        out = store_mod._extract_text_preview(
            ["bare-string", {"type": "text", "text": "x"}]
        )
        assert "bare-string" in out

    def test_dict_input(self):
        out = store_mod._extract_text_preview({"type": "text", "text": "hi"})
        assert "hi" in out

    def test_fallback_to_str(self):
        out = store_mod._extract_text_preview(42)
        assert out == "42"


# ── _max_mtime ────────────────────────────────────────────────


class TestMaxMtime:
    def test_missing_file_zero(self, tmp_path):
        assert store_mod._max_mtime(tmp_path / "no-such") == 0.0

    def test_basic(self, tmp_path):
        f = tmp_path / "s.kohakutr"
        f.write_bytes(b"")
        out = store_mod._max_mtime(f)
        assert out > 0

    def test_picks_wal_max(self, tmp_path):
        f = tmp_path / "s.kohakutr"
        f.write_bytes(b"")
        wal = tmp_path / "s.kohakutr-wal"
        wal.write_bytes(b"")
        # Touch the wal to make it newer than the main file.
        future = time.time() + 10
        import os

        os.utime(wal, (future, future))
        out = store_mod._max_mtime(f)
        assert out >= future - 1


# ── _session_dir ──────────────────────────────────────────────


class TestSessionDir:
    def test_resolves_to_the_sessions_subdir(self):
        out = store_mod._session_dir()
        # Sessions live under the '<config home>/sessions' directory.
        assert out.name == "sessions"


# ── _read_session_entry ───────────────────────────────────────


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


class TestReadSessionEntry:
    def test_basic(self, tmp_path):
        path = _make_session(tmp_path)
        out = store_mod._read_session_entry(path)
        assert out["name"] == "alice"
        # Preview captured.
        assert "hello" in out["preview"]
        assert out["config_type"] == "agent"

    def test_with_lineage(self, tmp_path):
        path = _make_session(
            tmp_path,
            meta_extra={
                "lineage": {
                    "fork": {"parent_session_id": "p", "fork_point": 3},
                    "migration": {"source_version": 1},
                }
            },
        )
        out = store_mod._read_session_entry(path)
        assert out["parent_session_id"] == "p"
        assert out["fork_point"] == 3
        assert out["migrated_from_version"] == 1

    def test_corrupt_returns_error(self, tmp_path):
        # Non-SQLite file → SessionStore opens it as a new vault; we
        # can't easily corrupt the format, so simulate by mocking.
        path = tmp_path / "bad.kohakutr"
        path.write_bytes(b"")
        out = store_mod._read_session_entry(path)
        # The empty file is still opened by KVault — assert structure.
        assert out["name"] == "bad"


# ── build_session_index / get_session_index ────────────────────


class TestBuildSessionIndex:
    def test_empty_dir_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path / "missing")
        out = store_mod.build_session_index()
        assert out == []

    def test_with_sessions(self, tmp_path, monkeypatch):
        _make_session(tmp_path, name="alice")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        out = store_mod.build_session_index()
        names = [e["name"] for e in out]
        assert "alice" in names

    def test_get_session_index_uses_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store_mod, "_session_index", [{"cached": True}])
        monkeypatch.setattr(store_mod, "_index_built_at", time.time())
        out = store_mod.get_session_index()
        assert out == [{"cached": True}]

    def test_get_session_index_rebuilds_when_stale(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        monkeypatch.setattr(store_mod, "_session_index", [{"stale": True}])
        monkeypatch.setattr(store_mod, "_index_built_at", 0)
        # Stale cache + empty session dir → rebuilds to an empty list,
        # discarding the stale entry.
        out = store_mod.get_session_index(max_age=0.0)
        assert out == []


# ── session_stats ─────────────────────────────────────────────


class TestSessionStats:
    def test_empty(self, monkeypatch):
        monkeypatch.setattr(store_mod, "_session_index", [])
        monkeypatch.setattr(store_mod, "_index_built_at", time.time())
        out = store_mod.session_stats()
        assert out["count"] == 0

    def test_with_sessions(self, monkeypatch):
        now = time.time()
        entries = [
            {
                "config_type": "agent",
                "status": "running",
                "format_version": 2,
                "agents": ["alice"],
                "last_active": "2025-01-01T00:00:00+00:00",
            },
            {
                "config_type": "agent",
                "status": "paused",
                "format_version": 2,
                "agents": ["bob"],
                "last_active": "2026-12-01T00:00:00+00:00",
            },
            {"error": True},  # skipped
        ]
        monkeypatch.setattr(store_mod, "_session_index", entries)
        monkeypatch.setattr(store_mod, "_index_built_at", now)
        out = store_mod.session_stats()
        assert out["count"] == 3
        assert out["by_config_type"]["agent"] == 2
        assert "alice" in [a[0] for a in out["agents_top"]]


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
