"""Extra coverage tests for :mod:`kohakuterrarium.studio.persistence.store`.

The legacy in-memory listing helpers (``build_session_index`` /
``get_session_index`` / ``session_stats`` / ``_read_session_entry`` /
``_extract_text_preview`` / ``_max_mtime``) have been removed in
favour of the sidecar at :mod:`studio.persistence.session_index`.
This file now covers only the remaining per-session helpers in
``store.py`` — the ones that the sidecar does NOT subsume.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import store as store_mod

# ── session_targets: fallback paths ─────────────────────────


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


# ── delete_session_files: extra paths ───────────────────────


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


# ── session_history_payload ─────────────────────────────────


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


# ── all_session_files_default ───────────────────────────────


class TestAllSessionFilesDefault:
    def test_lists_files_under_default_dir(self, tmp_path, monkeypatch):
        (tmp_path / "a.kohakutr").write_bytes(b"x")
        (tmp_path / "b.kt").write_bytes(b"x")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        names = {p.name for p in store_mod.all_session_files_default()}
        assert names == {"a.kohakutr", "b.kt"}


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
        real_path_stat = Path.stat

        def _path_stat(self, *a, **k):
            if self == bad:
                raise OSError("cannot stat")
            return real_path_stat(self, *a, **k)

        monkeypatch.setattr(Path, "stat", _path_stat)
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
