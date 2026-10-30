"""Studio persistence-store edge coverage around delete and listing.

Drives production helpers with a real closed :class:`SessionStore` plus the
existing delete-enrollment fixtures. Unlink retries go through the real
``Path.unlink`` I/O seam; listing/stat errors use real files whose ``stat``
raises. Lines that require a production hash-race or a magic empty
``last_exc`` after a PermissionError loop are named in the closest docstring.
"""

from pathlib import Path

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence import store as store_mod
from kohakuterrarium.studio.sessions.live import live_sessions


def _closed_store(tmp_path, name: str = "alice") -> Path:
    path = tmp_path / f"{name}.kohakutr"
    store = SessionStore(str(path), writer_lock=True)
    try:
        store.init_meta("sess", "agent", "/p", "/w", [name])
        store.append_event(name, "user_input", {"content": "hello"})
        store.flush()
    finally:
        store.close()
    return path


def _assert_gone(path: Path) -> None:
    assert not path.exists()


class TestUnlinkRetrySeam:
    def test_retries_permissionerror_then_unlinks(self, tmp_path, monkeypatch):
        path = _closed_store(tmp_path)
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        monkeypatch.setattr(store_mod.time, "sleep", lambda _s: None)
        real_unlink = Path.unlink
        calls = {"n": 0}

        def flaky_unlink(self, *args, **kwargs):
            if Path(self) == path or Path(self).name.startswith(path.name + ".orphan-"):
                if calls["n"] == 0:
                    calls["n"] += 1
                    raise PermissionError(13, "transient lock")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)
        deleted = store_mod.delete_session_files("alice")
        assert path in deleted or any(p.name.startswith(path.name) for p in deleted)
        _assert_gone(path)
        assert calls["n"] == 1

    def test_missing_file_during_retry_is_success(self, tmp_path, monkeypatch):
        path = _closed_store(tmp_path)
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        monkeypatch.setattr(store_mod.time, "sleep", lambda _s: None)
        real_unlink = Path.unlink

        def vanish_then_missing(self, *args, **kwargs):
            target = Path(self)
            if target.name.startswith(path.name + ".orphan-") or target == path:
                if target.exists():
                    real_unlink(self, *args, **kwargs)
                raise FileNotFoundError(target)
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", vanish_then_missing)
        deleted = store_mod.delete_session_files("alice")
        assert deleted
        _assert_gone(path)

    def test_exhausted_permissionerror_keeps_quarantine(self, tmp_path, monkeypatch):
        path = _closed_store(tmp_path)
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        monkeypatch.setattr(store_mod.time, "sleep", lambda _s: None)
        real_unlink = Path.unlink

        def always_locked(self, *args, **kwargs):
            target = Path(self)
            if target.name.startswith(path.name) or target == path:
                raise PermissionError(13, "still locked")
            return real_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", always_locked)
        deleted = store_mod.delete_session_files("alice")
        _assert_gone(path)
        orphans = list(tmp_path.glob(f"{path.name}.orphan-*"))
        assert len(orphans) == 1
        assert orphans[0] in deleted
        assert orphans[0].exists()

    def test_unlink_retry_exhausted_raises_last_permissionerror(
        self, tmp_path, monkeypatch
    ):
        """Direct I/O seam: five PermissionErrors re-raise the last one.

        The ``assert last_exc is not None`` on store.py:285 is the loop
        invariant after a PermissionError was stored; production cannot
        reach a raise without that assignment, so the assert is not
        independently exercisable without rewriting the helper.
        """
        path = tmp_path / "locked.bin"
        path.write_bytes(b"x")
        monkeypatch.setattr(store_mod.time, "sleep", lambda _s: None)
        attempts = {"n": 0}

        def always_locked(self, *args, **kwargs):
            attempts["n"] += 1
            raise PermissionError(13, f"locked-{attempts['n']}")

        monkeypatch.setattr(Path, "unlink", always_locked)
        with pytest.raises(PermissionError, match="locked-5"):
            store_mod._unlink_with_retry(path, attempts=5, base_delay=0.0)
        assert attempts["n"] == 5
        assert path.exists()


class TestDiskUsageListingErrors:
    def test_artifacts_dir_stat_oserror_is_skipped(self, tmp_path, monkeypatch):
        session = _closed_store(tmp_path, "alice")
        artifacts = tmp_path / "alice.artifacts"
        artifacts.mkdir()
        (artifacts / "clip.bin").write_bytes(b"media")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        real_is_dir = Path.is_dir

        def flaky_is_dir(self):
            if self == artifacts:
                raise OSError("cannot stat artifacts dir")
            return real_is_dir(self)

        monkeypatch.setattr(Path, "is_dir", flaky_is_dir)
        out = store_mod.disk_usage()
        assert out["count"] == 1
        assert out["session_bytes"] >= session.stat().st_size
        assert out["artifacts_bytes"] == 0

    def test_artifacts_file_not_dir_is_ignored(self, tmp_path, monkeypatch):
        _closed_store(tmp_path, "alice")
        fake = tmp_path / "not-a-dir.artifacts"
        fake.write_bytes(b"not-a-directory")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        out = store_mod.disk_usage()
        assert out["count"] == 1
        assert out["artifacts_bytes"] == 0

    def test_resolve_session_path_in_uses_explicit_directory(self, tmp_path):
        path = _closed_store(tmp_path, "alice")
        other = tmp_path / "other"
        other.mkdir()
        assert store_mod.resolve_session_path_in("alice", tmp_path) == path
        assert store_mod.resolve_session_path_in("alice", other) is None


class TestSessionTargetsAndHistory:
    def test_event_key_without_marker_is_skipped(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        try:
            store.init_meta("s1", "agent", "/p", "/w", [])
            store.append_event("alice", "user_input", {"content": "hi"})
            store.events["note"] = {"type": "stray", "ts": 1}
            store.flush()
            targets = store_mod.session_targets(store, store.load_meta())
            assert "alice" in targets
            assert "note" not in targets
        finally:
            store.close()

    def test_conversation_bytes_keys_are_decoded(self, tmp_path):
        path = tmp_path / "s.kohakutr"
        store = SessionStore(str(path))
        try:
            store.init_meta("s1", "agent", "/p", "/w", [])
            store.save_conversation("solo", [{"role": "user", "content": "hi"}])
            store.flush()
            original_keys = store.conversation.keys

            def bytes_keys(*args, **kwargs):
                for key in original_keys(*args, **kwargs):
                    yield key if isinstance(key, bytes) else key.encode("utf-8")

            store.conversation.keys = bytes_keys
            targets = store_mod.session_targets(store, store.load_meta())
            assert "solo" in targets
        finally:
            store.close()


class TestDeleteIndexAndFuzzyResolve:
    def test_index_purge_failure_does_not_undo_delete(self, tmp_path, monkeypatch):
        path = _closed_store(tmp_path, "alice")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        live_sessions().add(
            path, session_id="alice", session_dir=str(tmp_path), boot_id="b"
        )

        def boom_index(_session_dir):
            raise RuntimeError("index unavailable")

        monkeypatch.setattr(store_mod, "get_session_index_default", boom_index)
        deleted = store_mod.delete_session_files("alice")
        assert path in deleted or any(p.name.startswith("alice") for p in deleted)
        _assert_gone(path)
        assert live_sessions().get(path) is None

    def test_fuzzy_resolve_deletes_unique_prefix_match(self, tmp_path, monkeypatch):
        path = _closed_store(tmp_path, "alice-long")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        deleted = store_mod.delete_session_files("alice")
        assert path in deleted
        _assert_gone(path)

    def test_resolved_stem_without_versions_deletes_file(self, tmp_path, monkeypatch):
        path = tmp_path / "custom.session"
        path.write_bytes(b"session")
        monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path)
        monkeypatch.setattr(store_mod, "all_versions_for_session_default", lambda n: [])
        monkeypatch.setattr(store_mod, "resolve_session_path_default", lambda n: path)
        monkeypatch.setattr(store_mod, "normalize_session_stem", lambda p: "custom")
        deleted = store_mod.delete_session_files("custom")
        assert deleted == [path]
        _assert_gone(path)
