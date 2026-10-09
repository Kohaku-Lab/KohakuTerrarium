"""Unit tests for :mod:`kohakuterrarium.studio.sessions.live.tracking`."""

import kohakuterrarium.session.run_state as rs
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions.live import (
    forget,
    live_sessions,
    mark_live,
    track,
    untrack_store,
)
from kohakuterrarium.terrarium import Terrarium


def _store(tmp_path, name):
    return SessionStore(str(tmp_path / "sessions" / f"{name}.kohakutr"))


async def test_track_records_current_and_future_stores_as_live(tmp_path):
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    (tmp_path / "sessions").mkdir()
    first, second = _store(tmp_path, "first"), _store(tmp_path, "second")
    try:
        rs.freeze(first)
        engine._session_stores["g1"] = first
        track(engine)
        track(engine)
        engine._session_stores["g2"] = second
        rows = {r["session_id"]: r for r in live_sessions().rows()}
        assert set(rows) == {"g1", "g2"}
        assert rows["g1"]["session_dir"] == str(tmp_path / "sessions")
        assert rows["g1"]["boot_id"] == rs.BOOT_ID
        assert rs.read_lifecycle(first)["live"] is True
        assert not rs.is_frozen(first)
    finally:
        first.close(update_status=False)
        second.close(update_status=False)


def test_mark_live_skips_read_only_and_missing_stores(tmp_path):
    (tmp_path / "sessions").mkdir()
    writable = _store(tmp_path, "w")
    writable.close(update_status=False)
    readonly = SessionStore.open_readonly(tmp_path / "sessions" / "w.kohakutr")
    try:
        assert mark_live(None, "g", readonly) is None
        assert mark_live(None, "g", None) is None
        assert live_sessions().rows() == []
    finally:
        readonly.close(update_status=False)


def test_untrack_and_forget_drop_rows_and_record_the_user_stop(tmp_path):
    (tmp_path / "sessions").mkdir()
    store = _store(tmp_path, "s")
    try:
        mark_live(None, "g", store)
        assert live_sessions().get(store.path)["session_dir"] == str(
            tmp_path / "sessions"
        )
        untrack_store(store)
        assert live_sessions().get(store.path) is None
        assert rs.stop_reason(rs.read_lifecycle(store)) == rs.STOP_USER
        mark_live(None, "g", store)
        assert forget(store.path) is True
        assert forget(store.path) is False
        untrack_store(None)
    finally:
        store.close(update_status=False)
