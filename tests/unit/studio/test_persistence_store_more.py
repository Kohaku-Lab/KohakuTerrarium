"""Extra coverage tests for :mod:`kohakuterrarium.studio.persistence.store`."""

import os
import time
from unittest.mock import MagicMock


from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import store as store_mod

# ── _extract_text_preview ────────────────────────────────────


class TestExtractTextPreview:
    def test_none(self):
        assert store_mod._extract_text_preview(None) == ""

    def test_string(self):
        assert store_mod._extract_text_preview("hello") == "hello"

    def test_string_truncates(self):
        assert len(store_mod._extract_text_preview("x" * 500, limit=10)) == 10

    def test_list_with_strings(self):
        assert store_mod._extract_text_preview(["a", "b"]) == "a b"

    def test_list_with_text_parts(self):
        assert store_mod._extract_text_preview([{"type": "text", "text": "hi"}]) == "hi"

    def test_list_with_image_part(self):
        out = store_mod._extract_text_preview([{"type": "image_url", "url": "x"}])
        assert "[image]" in out

    def test_list_with_file_part(self):
        out = store_mod._extract_text_preview([{"type": "file", "name": "f"}])
        assert "[file]" in out

    def test_list_with_unknown_part(self):
        out = store_mod._extract_text_preview([{"type": "video", "url": "x"}])
        assert "[video]" in out

    def test_dict_single_part(self):
        out = store_mod._extract_text_preview({"type": "text", "text": "hi"})
        assert out == "hi"

    def test_other_type_str_fallback(self):
        out = store_mod._extract_text_preview(42)
        assert out == "42"


# ── _read_session_entry ──────────────────────────────────────


class TestReadSessionEntry:
    def test_basic(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        store.append_event("alice", "user_input", {"content": "hello world"})
        store.flush()
        store.close()
        out = store_mod._read_session_entry(path)
        assert out["name"]
        assert out["agents"] == ["alice"]
        # Preview should pick up the user_input content.
        assert out["preview"] == "hello world"

    def test_corrupt_returns_error_entry(self, tmp_path):
        path = tmp_path / "bad.kohakutr"
        path.write_bytes(b"not-a-sqlite")
        out = store_mod._read_session_entry(path)
        assert out.get("error") is True
        assert "name" in out


# ── session_targets / delete_session_files ──────────────────


class TestSessionTargetsFallback:
    def test_events_fallback(self, tmp_path):
        """Targets discovered from event keys when meta has none."""
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", [])
        store.append_event("alice", "user_input", {"content": "hi"})
        store.flush()
        try:
            targets = store_mod.session_targets(store, store.load_meta())
            # No meta agents — the fallback path discovers the agent
            # namespace 'alice' from the recorded event keys.
            assert "alice" in targets
        finally:
            store.close()


class TestDeleteSessionFiles:
    def test_no_files_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(store_mod, "all_versions_for_session_default", lambda n: [])
        monkeypatch.setattr(store_mod, "resolve_session_path_default", lambda n: None)
        assert store_mod.delete_session_files("ghost") == []

    def test_resolved_fallback(self, monkeypatch, tmp_path):
        # Custom: first call returns empty, second call (after resolve)
        # returns empty too, so fallback to [resolved].
        f = tmp_path / "x.kohakutr"
        f.write_bytes(b"x")
        calls = [iter([[], []])]

        def _all_versions(n):
            return next(calls[0])

        monkeypatch.setattr(
            store_mod, "all_versions_for_session_default", _all_versions
        )
        monkeypatch.setattr(store_mod, "resolve_session_path_default", lambda n: f)
        monkeypatch.setattr(store_mod, "normalize_session_stem", lambda p: "x")
        out = store_mod.delete_session_files("x")
        assert out == [f]
        assert not f.exists()

    def test_unlinks_each(self, monkeypatch, tmp_path):
        a = tmp_path / "a.kohakutr"
        a.write_bytes(b"x")
        b = tmp_path / "b.kohakutr"
        b.write_bytes(b"y")
        monkeypatch.setattr(
            store_mod, "all_versions_for_session_default", lambda n: [a, b]
        )
        out = store_mod.delete_session_files("x")
        assert sorted(out) == sorted([a, b])
        assert not a.exists() and not b.exists()


# ── session_history_payload ──────────────────────────────────


class TestSessionHistoryPayload:
    def test_channel_target(self):
        store = MagicMock()
        store.get_channel_messages.return_value = [
            {"sender": "alice", "content": "hi", "ts": 1}
        ]
        out = store_mod.session_history_payload(store, "ch:chat")
        assert out["target"] == "ch:chat"
        assert out["events"][0]["channel"] == "chat"

    def test_agent_target_resumable_events(self):
        store = MagicMock()
        store.get_resumable_events.return_value = [{"type": "x"}]
        store.load_conversation.return_value = [{"role": "user"}]
        out = store_mod.session_history_payload(store, "alice")
        assert out["target"] == "alice"
        assert out["events"][0]["type"] == "x"

    def test_agent_target_fallback_get_events(self):
        store = MagicMock(spec=["get_events", "load_conversation"])
        store.get_events.return_value = []
        store.load_conversation.return_value = None
        out = store_mod.session_history_payload(store, "alice")
        assert out["target"] == "alice"


# ── _max_mtime ──────────────────────────────────────────────


class TestMaxMtime:
    def test_no_sidecars(self, tmp_path):
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"x")
        out = store_mod._max_mtime(p)
        assert out > 0

    def test_missing_path_returns_zero(self, tmp_path):
        out = store_mod._max_mtime(tmp_path / "ghost")
        assert out == 0.0

    def test_picks_max_with_sidecar(self, tmp_path):
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"x")
        old = p.stat().st_mtime
        wal = tmp_path / "x.kohakutr-wal"
        wal.write_bytes(b"y")
        # Bump WAL mtime to be newer.
        os.utime(wal, (old + 10, old + 10))
        out = store_mod._max_mtime(p)
        assert out >= old + 10


# ── session_stats / disk_usage ──────────────────────────────


class TestSessionStats:
    def test_empty_index(self, monkeypatch):
        monkeypatch.setattr(store_mod, "get_session_index", lambda: [])
        out = store_mod.session_stats()
        assert out["count"] == 0

    def test_with_entries(self, monkeypatch):
        monkeypatch.setattr(
            store_mod,
            "get_session_index",
            lambda: [
                {
                    "config_type": "agent",
                    "status": "running",
                    "format_version": 2,
                    "agents": ["alice"],
                    "last_active": "2026-05-13T00:00:00+00:00",
                },
                {"error": True},  # skipped
            ],
        )
        out = store_mod.session_stats()
        assert out["count"] == 2
        assert out["by_config_type"] == {"agent": 1}


class TestDiskUsage:
    def test_missing_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(store_mod, "_session_dir", lambda: tmp_path / "ghost")
        out = store_mod.disk_usage()
        assert out["count"] == 0

    def test_basic(self, monkeypatch, tmp_path):
        # Create a session file + sidecar.
        f = tmp_path / "s.kohakutr"
        f.write_bytes(b"data")
        wal = tmp_path / "s.kohakutr-wal"
        wal.write_bytes(b"sidecar")
        monkeypatch.setattr(store_mod, "_session_dir", lambda: tmp_path)
        monkeypatch.setattr(store_mod, "pick_canonical_per_session", lambda d: [f])
        out = store_mod.disk_usage()
        assert out["count"] >= 1
        assert out["total_bytes"] > 0


# ── get_session_index caching ───────────────────────────────


class TestGetSessionIndex:
    def test_returns_cached(self, monkeypatch):
        store_mod._session_index = [{"name": "x"}]
        store_mod._index_built_at = time.time()
        out = store_mod.get_session_index(max_age=300)
        assert out == [{"name": "x"}]

    def test_rebuilds_when_stale(self, monkeypatch):
        store_mod._session_index = []
        store_mod._index_built_at = 0
        rebuilt = []
        monkeypatch.setattr(
            store_mod,
            "build_session_index",
            lambda: rebuilt.append("call") or [{"name": "fresh"}],
        )
        out = store_mod.get_session_index(max_age=0)
        assert rebuilt
        assert out == [{"name": "fresh"}]


# ── _max_mtime: sidecar stat failure ────────────────────────


class TestMaxMtimeSidecarFailure:
    def test_unreadable_sidecar_is_skipped(self, tmp_path, monkeypatch):
        # A -wal sidecar exists but os.stat on it raises — the helper
        # must skip it and still return the main file's mtime.
        p = tmp_path / "x.kohakutr"
        p.write_bytes(b"x")
        wal = tmp_path / "x.kohakutr-wal"
        wal.write_bytes(b"y")
        real_stat = os.stat

        def _stat(path, *a, **k):
            if str(path).endswith("-wal"):
                raise OSError("unreadable sidecar")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(store_mod.os, "stat", _stat)
        out = store_mod._max_mtime(p)
        # Main file mtime still reported despite the sidecar failure.
        assert out == p.stat().st_mtime


# ── _read_session_entry: preview-read failure ───────────────


class TestReadSessionEntryPreviewFailure:
    def test_preview_read_failure_leaves_blank_preview(self, tmp_path, monkeypatch):
        # The session file is valid, but reading resumable events for the
        # preview raises. The entry must still be returned (with all its
        # meta), just with an empty preview — not an error entry.
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", ["alice"])
        store.append_event("alice", "user_input", {"content": "hello"})
        store.flush()
        store.close()

        real_get = SessionStore.get_resumable_events

        def _boom(self, agent_name, **k):
            raise RuntimeError("preview read exploded")

        monkeypatch.setattr(SessionStore, "get_resumable_events", _boom)
        try:
            out = store_mod._read_session_entry(path)
        finally:
            monkeypatch.setattr(SessionStore, "get_resumable_events", real_get)
        # Not an error entry — meta survived, preview just blank.
        assert out.get("error") is not True
        assert out["agents"] == ["alice"]
        assert out["preview"] == ""


# ── all_session_files_default ───────────────────────────────


class TestAllSessionFilesDefault:
    def test_lists_files_under_default_dir(self, tmp_path, monkeypatch):
        (tmp_path / "a.kohakutr").write_bytes(b"x")
        (tmp_path / "b.kt").write_bytes(b"x")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        names = {p.name for p in store_mod.all_session_files_default()}
        assert names == {"a.kohakutr", "b.kt"}


# ── session_stats: timestamp parsing + recency buckets ──────


class TestSessionStatsRecency:
    def test_recency_buckets_and_unparseable_timestamps(self, monkeypatch):
        now = time.time()
        day = 86400
        monkeypatch.setattr(
            store_mod,
            "get_session_index",
            lambda: [
                # ~12h ago → "1d" bucket
                {
                    "config_type": "agent",
                    "status": "ok",
                    "last_active": _iso(now - day // 2),
                },
                # ~3d ago → "7d" bucket
                {
                    "config_type": "agent",
                    "status": "ok",
                    "last_active": _iso(now - 3 * day),
                },
                # ~40d ago → "older" bucket
                {
                    "config_type": "agent",
                    "status": "ok",
                    "last_active": _iso(now - 40 * day),
                },
                # blank timestamp → contributes to nothing recency-wise
                {"config_type": "agent", "status": "ok", "last_active": ""},
                # garbage timestamp → ValueError path, also no recency bump
                {
                    "config_type": "agent",
                    "status": "ok",
                    "last_active": "not-a-date",
                },
            ],
        )
        out = store_mod.session_stats()
        assert out["by_recency"]["1d"] == 1
        assert out["by_recency"]["7d"] == 1
        assert out["by_recency"]["older"] == 1
        # The blank + garbage timestamps never landed in a bucket.
        assert (
            out["by_recency"]["1d"]
            + out["by_recency"]["7d"]
            + out["by_recency"]["30d"]
            + out["by_recency"]["older"]
        ) == 3


# ── disk_usage: stat failures ───────────────────────────────


class TestDiskUsageStatFailures:
    def test_canonical_file_stat_failure_is_skipped(self, tmp_path, monkeypatch):
        good = tmp_path / "good.kohakutr"
        good.write_bytes(b"good-data")
        bad = tmp_path / "bad.kohakutr"
        bad.write_bytes(b"bad")
        monkeypatch.setattr(store_mod, "_session_dir", lambda: tmp_path)
        monkeypatch.setattr(
            store_mod, "pick_canonical_per_session", lambda d: [bad, good]
        )
        from pathlib import Path as _P

        real_path_stat = _P.stat

        def _path_stat(self, *a, **k):
            if self == bad:
                raise OSError("cannot stat")
            return real_path_stat(self, *a, **k)

        monkeypatch.setattr(_P, "stat", _path_stat)
        out = store_mod.disk_usage()
        # The unreadable file contributed nothing; the good one did.
        assert out["total_bytes"] == len(b"good-data")

    def test_sidecar_stat_failure_is_skipped(self, tmp_path, monkeypatch):
        f = tmp_path / "s.kohakutr"
        f.write_bytes(b"main")
        wal = tmp_path / "s.kohakutr-wal"
        wal.write_bytes(b"sidecar-bytes")
        monkeypatch.setattr(store_mod, "_session_dir", lambda: tmp_path)
        monkeypatch.setattr(store_mod, "pick_canonical_per_session", lambda d: [f])
        real_stat = os.stat

        def _stat(path, *a, **k):
            if str(path).endswith("-wal"):
                raise OSError("sidecar stat failed")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(store_mod.os, "stat", _stat)
        out = store_mod.disk_usage()
        # Sidecar bytes excluded (its stat raised), main file counted.
        assert out["total_bytes"] == len(b"main")


# ── session_targets: conversation-table fallback ────────────


class TestSessionTargetsConversationFallback:
    def test_targets_discovered_from_conversation_keys(self, tmp_path):
        # No meta agents AND no event keys with ':e' — the last fallback
        # walks the conversation table's keys for target names.
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        store.init_meta("s1", "agent", "/p", "/w", [])
        # Persist a conversation snapshot under the 'solo' namespace
        # without writing any ':e'-keyed events.
        store.save_conversation("solo", [{"role": "user", "content": "hi"}])
        store.flush()
        try:
            targets = store_mod.session_targets(store, store.load_meta())
            assert "solo" in targets
        finally:
            store.close()


def _iso(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
