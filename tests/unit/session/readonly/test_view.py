"""SessionReadView reads through its own read-only KohakuVault handles."""

import asyncio
import logging
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from kohakuterrarium.session.readonly import view as readonly_view
from kohakuterrarium.session.errors import SessionNotReadyError, SessionViewClosedError
from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.readonly.view import SessionReadView
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_LEGACY_ENROLLED,
    REASON_LEGACY_RELEASE_UNCERTAIN,
    REASON_OK,
    try_fast,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.session.vault.handles import is_disposed

_T = 5.0


def _populate(store):
    store.init_meta("sid", "agent", "", "", ["alice"])
    store.meta["nested"] = {"items": [None, True, 4.5, "汉字"]}
    store.append_event("discovered", "user_input", {"content": "new agent"})
    store.append_event("alice:attached:helper:1", "text", {"content": "private"})
    store.append_event("alice", "user_input", {"content": "first"})
    store.append_event("alice", "user_input", {"content": "second"})
    store.save_conversation("alice", [{"role": "user", "content": "second"}])


def _closed_session(path: Path) -> Path:
    with closing(SessionStore(path)) as store:
        _populate(store)
    return path


def _family(path: Path) -> dict[str, tuple[bytes, int]]:
    return {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in path.parent.iterdir()
        if p.name.startswith(path.name)
    }


def _assert_idle(path) -> None:
    admitted = try_fast(path, eligible=True)
    assert admitted.reason == REASON_OK
    admitted.lease.finish(source_released=True)


def _corrupt(path: Path, table: str, key: bytes) -> None:
    with closing(sqlite3.connect(path)) as conn:
        value = conn.execute(
            f"SELECT value FROM {table} WHERE key=?", (key,)
        ).fetchone()[0]
        broken = bytearray(value)
        broken[2] ^= 0x7F
        conn.execute(f"UPDATE {table} SET value=? WHERE key=?", (bytes(broken), key))
        conn.commit()


def _unreadable(caplog):
    return [r for r in caplog.records if r.getMessage() == "Unreadable session value"]


class TestReads:
    def test_live_store_view_sees_buffered_and_later_rows(self, tmp_path):
        path = tmp_path / "snapshot %20 #.kohakutr"
        with closing(SessionStore(path)) as store:
            _populate(store)
            store.append_event("alice", "text", {"content": "buffered only"})
            assert store._unflushed_event_count > 0
            with SessionReadView(path.as_uri()) as reader:
                assert store._unflushed_event_count == 0
                meta = reader.load_meta()
                assert meta["agents"] == ["alice", "discovered"]
                assert meta["nested"] == {"items": [None, True, 4.5, "汉字"]}
                assert reader.load_meta(discover_agents=False)["agents"] == ["alice"]
                store.meta["nested"] = {"items": ["new value"]}
                assert reader.get("meta", "nested") == {"items": ["new value"]}
                assert reader.get("state", "missing", "default") == "default"
                assert reader.get("conversation", "alice") == [
                    {"role": "user", "content": "second"}
                ]
                contents = [
                    e["content"] for _, e in reader.items("events", prefix="alice:e")
                ]
                assert contents == ["first", "second", "buffered only"]
                # The view never borrows the live store's connections.
                assert reader._handles["meta"] is not store.meta
                with pytest.raises(ValueError, match="unsupported session table"):
                    reader.get("other", "key")
                with pytest.raises(ValueError, match="unsupported session table"):
                    list(reader.items("other"))

    def test_prefix_items_are_in_key_order(self, tmp_path):
        path = _closed_session(tmp_path / "order.kohakutr")
        with SessionReadView(path) as reader:
            items = list(reader.items("events", prefix="alice:e"))
            assert [e["content"] for _, e in items] == ["first", "second"]
            assert [k for k, _ in items] == sorted(k for k, _ in items)
            assert all(k.startswith("alice:e") for k, _ in items)

    def test_closed_session_view_leaves_the_file_family_untouched(self, tmp_path):
        path = _closed_session(tmp_path / "closed.kohakutr")
        before = _family(path)
        with SessionReadView(path) as reader:
            assert reader.get("meta", "nested") == {"items": [None, True, 4.5, "汉字"]}
            assert reader.load_meta()["agents"] == ["alice", "discovered"]
            assert reader.get("conversation", "alice")[0]["content"] == "second"
        assert _family(path) == before
        _assert_idle(path)

    def test_view_over_a_live_writer_in_another_connection(self, tmp_path):
        path = tmp_path / "later.kohakutr"
        store = SessionStore(path)
        _populate(store)
        with SessionReadView(path) as reader:
            assert reader.get("meta", "nested")["items"][-1] == "汉字"
        store.close()
        with SessionReadView(path) as reader:
            assert reader.load_meta()["agents"] == ["alice", "discovered"]

    def test_interleaved_generators_and_gets(self, tmp_path):
        path = _closed_session(tmp_path / "interleave.kohakutr")
        with SessionReadView(path) as reader:
            events = reader.items("events")
            keys = reader._event_keys()
            meta = reader.items("meta")
            event1, key1 = next(events), next(keys)
            nested = reader.get("meta", "nested")
            event2, meta1, key2 = next(events), next(meta), next(keys)
            seen = [event1, event2, *list(events)]
            assert [item[1]["content"] for item in seen] == [
                "private",
                "first",
                "second",
                "new agent",
            ]
            assert [key1, key2, *list(keys)] == [item[0] for item in seen]
            assert meta1[0] and dict(meta)["nested"] == nested

    def test_missing_tables_and_foreign_files_read_as_empty(self, tmp_path):
        empty = tmp_path / "empty.kohakutr"
        sqlite3.connect(empty).close()
        foreign = tmp_path / "unrelated.sqlite"
        with closing(sqlite3.connect(foreign)) as conn:
            conn.execute("CREATE TABLE unrelated(x INTEGER)")
            conn.commit()
        for path in (empty, foreign):
            with SessionReadView(path) as reader:
                assert reader.get("meta", "nested", "DEFAULT") == "DEFAULT"
                assert list(reader.items("conversation")) == []
                assert reader.load_meta() == {"agents": []}
            _assert_idle(path)

    def test_event_keys_without_a_namespace_marker_are_ignored(self, tmp_path):
        path = _closed_session(tmp_path / "orphan.kohakutr")
        with closing(sqlite3.connect(path)) as conn:
            payload = conn.execute("SELECT value FROM events LIMIT 1").fetchone()[0]
            conn.execute(
                "INSERT INTO events (key, value) VALUES (?, ?)", (b"orphan", payload)
            )
            conn.commit()
        with SessionReadView(path) as reader:
            assert reader.load_meta()["agents"] == ["alice", "discovered"]

    def test_corrupt_rows_are_logged_and_skipped(self, tmp_path, caplog):
        path = _closed_session(tmp_path / "corrupt.kohakutr")
        _corrupt(path, "meta", b"nested")
        _corrupt(path, "events", b"alice:e000000")
        caplog.set_level(
            logging.WARNING, logger="kohakuterrarium.session.readonly.view"
        )
        with SessionReadView(path) as reader:
            assert reader.get("meta", "nested", "DEFAULT") == "DEFAULT"
            items = list(reader.items("events", prefix="alice:e"))
            assert [e["content"] for _, e in items] == ["second"]
            assert reader.get("meta", "session_id") == "sid"
        records = _unreadable(caplog)
        assert {getattr(r, "table", None) for r in records} == {"meta", "events"}
        assert {getattr(r, "key", None) for r in records} == {"nested", "alice:e000000"}
        _assert_idle(path)

    def test_missing_view_does_not_create_database(self, tmp_path):
        path = tmp_path / "missing.kohakutr"
        with pytest.raises(FileNotFoundError):
            SessionReadView(path)
        assert not path.exists()
        assert GATE.snapshot(path).registered is False


class TestLifecycle:
    def test_closed_view_rejects_queries_and_stops_generators(self, tmp_path):
        path = _closed_session(tmp_path / "closed-get.kohakutr")
        reader = SessionReadView(path)
        events, keys = reader.items("events"), reader._event_keys()
        assert next(events)[0] and next(keys)
        handles = [h for h in reader._handles.values() if h is not None]
        reader.close()
        assert all(is_disposed(h) for h in handles)
        assert list(events) == [] and list(keys) == []
        for call in (
            lambda: reader.get("meta", "nested"),
            lambda: list(reader.items("events")),
            lambda: list(reader._event_keys()),
            reader.load_meta,
        ):
            with pytest.raises(SessionViewClosedError):
                call()
        reader.close()
        _assert_idle(path)

    def test_view_backs_off_while_a_store_is_still_opening(self, tmp_path, monkeypatch):
        path = tmp_path / "opening.kohakutr"
        SessionStore(path).close()
        outcome = {}
        real_open = SessionStore._open_tables

        def open_and_read(self):
            try:
                SessionReadView(path)
            except SessionNotReadyError as exc:
                outcome["error"] = exc
            real_open(self)

        monkeypatch.setattr(SessionStore, "_open_tables", open_and_read)
        SessionStore(path).close()
        assert isinstance(outcome["error"], SessionNotReadyError)

    def test_exit_keeps_body_error_and_raises_close_error(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "exit.kohakutr")
        close_error = RuntimeError("close failed")

        def failing(handle):
            handle.close()
            raise close_error

        monkeypatch.setattr(readonly_view, "dispose", failing)
        with pytest.raises(KeyError) as caught:
            with SessionReadView(path) as reader:
                reader.get("meta", "nested")
                raise KeyError("body")
        assert caught.value.__cause__ is close_error
        with pytest.raises(RuntimeError, match="close failed"):
            with SessionReadView(path) as reader:
                reader.get("meta", "nested")
        assert GATE.snapshot(path).legacy_uncertain
        assert try_fast(path, eligible=True).reason == REASON_LEGACY_RELEASE_UNCERTAIN

    def test_open_failure_holds_nothing(self, tmp_path):
        path = tmp_path / "garbage.kohakutr"
        path.write_bytes(b"not a database")
        with SessionReadView(path) as reader:
            with pytest.raises(Exception, match="not a database"):
                reader.get("meta", "nested")
        snap = GATE.snapshot(path)
        assert not snap.legacy_uncertain and snap.legacy_active == 0
        lease = try_fast(path, eligible=True).lease
        assert lease is not None
        lease.finish(source_released=True)


class TestOccupancy:
    def test_view_enrolls_legacy_and_declines_new(self, tmp_path):
        path = _closed_session(tmp_path / "enroll.kohakutr")
        with SessionReadView(path) as reader:
            assert reader.get("meta", "session_id") == "sid"
            assert GATE.snapshot(path).legacy_active == 1
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_ENROLLED
        assert GATE.snapshot(path).registered is False

    def test_store_memory_and_view_nest(self, tmp_path):
        path = tmp_path / "nest.kohakutr"
        store = SessionStore(path)
        memory = SessionMemory(str(path))
        try:
            _populate(store)
            with SessionReadView(path) as reader:
                assert GATE.snapshot(path).legacy_active == 3
                assert reader.get("meta", "session_id") == "sid"
            assert GATE.snapshot(path).legacy_active == 2
        finally:
            memory.close()
            store.close()
        _assert_idle(path)

    def test_view_waits_for_new_occupant_before_opening(self, tmp_path, monkeypatch):
        path = _closed_session(tmp_path / "wait.kohakutr")
        occupying = try_fast(path, eligible=True)
        opened = []
        real_open = readonly_view.open_read_table

        def spy(*args, **kwargs):
            opened.append(GATE.snapshot(path).new_active)
            return real_open(*args, **kwargs)

        monkeypatch.setattr(readonly_view, "open_read_table", spy)
        results = []

        def read():
            with SessionReadView(path) as reader:
                results.append(reader.get("meta", "session_id"))

        worker = threading.Thread(target=read)
        worker.start()
        try:
            deadline = time.monotonic() + _T
            while (
                time.monotonic() < deadline and GATE.snapshot(path).legacy_pending != 1
            ):
                time.sleep(0.01)
            assert GATE.snapshot(path).legacy_pending == 1 and not opened
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(_T)
        assert results == ["sid"] and opened == [0]
        _assert_idle(path)

    def test_cancelled_worker_keeps_lease_until_close(self, tmp_path):
        path = _closed_session(tmp_path / "cancel.kohakutr")
        entered, release, finished = (threading.Event() for _ in range(3))

        def hold():
            with SessionReadView(path) as reader:
                reader.get("meta", "session_id")
                entered.set()
                assert release.wait(_T)
            finished.set()

        async def run():
            task = asyncio.create_task(asyncio.to_thread(hold))
            assert await asyncio.to_thread(entered.wait, _T)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert GATE.snapshot(path).legacy_active == 1
            release.set()
            assert await asyncio.to_thread(finished.wait, _T)

        asyncio.run(run())
        _assert_idle(path)
