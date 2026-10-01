"""Event key-index regressions against real native session tables."""

import threading
from concurrent.futures import ThreadPoolExecutor

from kohakuterrarium.session.store import SessionStore


class ObservedTable:
    def __init__(self, table, on_read):
        self.table = table
        self.on_read = on_read

    def __getattr__(self, name):
        return getattr(self.table, name)

    def __getitem__(self, key):
        self.on_read()
        return self.table[key]


def test_failed_scan_is_retried_and_cached_payload_is_fresh(tmp_path):
    store = SessionStore(tmp_path / "s.kohakutr")
    try:
        key, eid = store.append_event("a", "text", {"content": "original"})
        store._flush_events_cache()
        failed = False

        def fail_once():
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("transient read failure")

        table = ObservedTable(store.events, fail_once)
        assert store._event_keys.get(table, "a", eid) is None
        assert store._event_keys.get(table, "a", eid)["content"] == "original"
        event = store.events[key]
        event["content"] = "updated"
        store.events[key] = event
        assert store.get_event_by_id("a", eid)["content"] == "updated"
        event["event_id"] = 777
        store.events[key] = event
        assert store.get_event_by_id("a", eid) is None
        assert store.get_event_by_id("a", 777)["content"] == "updated"
    finally:
        store.close()


def test_append_during_cold_scan_does_not_publish_stale_index(tmp_path):
    store = SessionStore(tmp_path / "s.kohakutr")
    entered, release = threading.Event(), threading.Event()
    try:
        _, eid = store.append_event("a", "text", {"content": "first"})
        store._flush_events_cache()

        def pause_read():
            entered.set()
            assert release.wait(5)

        table = ObservedTable(store.events, pause_read)
        with ThreadPoolExecutor(max_workers=1) as executor:
            reading = executor.submit(store._event_keys.get, table, "a", eid)
            try:
                assert entered.wait(3)
                _, appended = store.append_event("a", "text", {"content": "next"})
            finally:
                release.set()
            assert reading.result(timeout=3)["content"] == "first"
        assert store.get_event_by_id("a", appended)["content"] == "next"
    finally:
        release.set()
        store.close()


def test_reopen_and_counter_restore_rebuild_index(tmp_path):
    path = tmp_path / "s.kohakutr"
    store = SessionStore(path)
    try:
        store.append_event("a", "text", {"event_id": 500, "content": "first"})
        store.append_event("a", "text", {"event_id": 10, "content": "second"})
        assert store.get_event_by_id("a", 500)["content"] == "first"
        # Bulk-copy paths restore counters after writing directly to tables.
        store.events["a:e000002"] = {"event_id": 42, "content": "copied"}
        store._restore_counters()
        assert store.get_event_by_id("a", 42)["content"] == "copied"
    finally:
        store.close()
    reopened = SessionStore.open_readonly(path)
    try:
        assert reopened.get_event_by_id("a", 10)["content"] == "second"
        assert reopened.get_event_by_id("a", 42)["content"] == "copied"
        assert reopened.get_event_by_id("missing", 42) is None
    finally:
        reopened.close()


def test_duplicate_ids_keep_native_key_order_across_sequence_width(tmp_path):
    store = SessionStore(tmp_path / "s.kohakutr")
    try:
        store._event_seq["a"] = 999999
        store.append_event("a", "text", {"event_id": 42, "content": "first"})
        assert store.get_event_by_id("a", 42)["content"] == "first"
        store.append_event("a", "text", {"event_id": 42, "content": "second"})
        expected = next(e for e in store.get_events("a") if e["event_id"] == 42)
        assert store.get_event_by_id("a", 42) == expected
        store._event_keys.clear()
        assert store.get_event_by_id("a", 42) == expected
    finally:
        store.close()
