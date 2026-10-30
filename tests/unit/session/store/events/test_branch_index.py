# Tests for BranchIndex and session branch maxima publication.

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

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

    def get_many(self, keys, default=None):
        self.on_read()
        return self.table.get_many(keys, default)

    def __setitem__(self, key, value):
        self.table[key] = value


class SignalledLock:
    def __init__(self, real_lock):
        self.real_lock = real_lock
        self.attempted = threading.Event()
        self.reader_id = None

    def acquire(self, *args, **kwargs):
        if threading.get_ident() == self.reader_id:
            self.attempted.set()
        return self.real_lock.acquire(*args, **kwargs)

    def release(self, *args, **kwargs):
        return self.real_lock.release(*args, **kwargs)

    def __enter__(self):
        if threading.get_ident() == self.reader_id:
            self.attempted.set()
        return self.real_lock.__enter__()

    def __exit__(self, *args):
        return self.real_lock.__exit__(*args)


def test_maxima_survive_append_mirror_fork_and_reopen(tmp_path):
    path = tmp_path / "parent.kohakutr"
    store = SessionStore(path)
    original_table = store.events
    reads = []
    try:
        store.append_event(
            "a", "user_message", {"content": "original"}, turn_index=1, branch_id=2
        )
        store.append_event(
            "b", "user_message", {"content": "other"}, turn_index=1, branch_id=9
        )
        store.events = ObservedTable(store.events, lambda: reads.append(1))
        assert store.max_branch_id("a", 1) == 2
        assert reads
        reads.clear()
        for turn in (1, 2, 3):
            assert store.max_branch_id("a", turn) == (2 if turn == 1 else 0)
        assert reads == []
        _, cut = store.append_event(
            "a",
            "user_message",
            {"content": "mirrored", "event_id": 500},
            turn_index=2,
            branch_id=7,
        )
        replaced_key, _ = store.append_event("a", "text", {}, turn_index=1, branch_id=4)
        assert store.max_branch_id("a", 1) == 4
        assert store.max_branch_id("a", 2) == 7
        assert reads == []
        child = store.fork(str(tmp_path / "child.kohakutr"), at_event_id=cut)
        try:
            assert child.max_branch_id("a", 1) == 2
            assert child.max_branch_id("a", 2) == 7
            assert child.max_branch_id("b", 1) == 9
        finally:
            child.close()
        store.events[replaced_key] = {
            "type": "user_message",
            "event_id": 600,
            "turn_index": 3,
            "branch_id": 8,
        }
        store._restore_counters()
        assert store.max_branch_id("a", 3) == 8
    finally:
        store.events = original_table
        store.close()
    reopened = SessionStore.open_readonly(path)
    try:
        assert reopened.max_branch_id("a", 1) == 2
        assert reopened.max_branch_id("a", 3) == 8
        assert reopened.max_branch_id("missing", 1) == 0
    finally:
        reopened.close()


def test_failed_cold_read_does_not_cache_missing_branches(tmp_path):
    store = SessionStore(tmp_path / "s.kohakutr")
    original_table = store.events
    try:
        store.append_event("a", "text", {}, turn_index=1, branch_id=5)
        failed = False

        def fail_once():
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("transient read failure")

        store.events = ObservedTable(store.events, fail_once)
        with pytest.raises(OSError, match="transient"):
            store.max_branch_id("a", 1)
        assert store.max_branch_id("a", 1) == 5
    finally:
        store.events = original_table
        store.close()


def test_append_during_cold_scan_returns_latest_maximum(tmp_path):
    store = SessionStore(tmp_path / "s.kohakutr")
    original_table = store.events
    entered, release = threading.Event(), threading.Event()
    try:
        store.append_event("a", "text", {}, turn_index=1, branch_id=1)
        store._flush_events_cache()

        def pause_read():
            entered.set()
            assert release.wait(5)

        store.events = ObservedTable(store.events, pause_read)
        with ThreadPoolExecutor(max_workers=1) as executor:
            reading = executor.submit(store.max_branch_id, "a", 1)
            try:
                assert entered.wait(3)
                store.append_event("a", "text", {}, turn_index=1, branch_id=6)
            finally:
                release.set()
            assert reading.result(timeout=3) == 6
        assert store.max_branch_id("a", 1) == 6
    finally:
        release.set()
        store.events = original_table
        store.close()


@pytest.mark.parametrize("warm", [True, False])
def test_query_waits_for_native_write_publication(tmp_path, warm):
    store = SessionStore(tmp_path / f"query_wait_{warm}.kohakutr", writer_lock=True)
    original_table = store.events
    # A query flushes the append pipeline first, so it waits on that lock.
    signalled_lock = SignalledLock(store._flush._lock)
    store._flush._lock = signalled_lock

    written, release = threading.Event(), threading.Event()

    class PausedTable:
        def __init__(self, table):
            self.table = table
            self.pause = False

        def __getattr__(self, name):
            return getattr(self.table, name)

        def __getitem__(self, key):
            return self.table[key]

        def __setitem__(self, key, value):
            self.table[key] = value
            if self.pause:
                written.set()
                assert release.wait(5)

    paused = PausedTable(original_table)
    store.events = paused
    try:
        store.append_event("alice", "text", {}, turn_index=1, branch_id=1)
        if warm:
            assert store.max_branch_id("alice", 1) == 1
        paused.pause = True
        with ThreadPoolExecutor(max_workers=2) as executor:
            writer = executor.submit(
                store.append_event, "alice", "text", {}, turn_index=1, branch_id=2
            )
            assert written.wait(5)

            def query():
                signalled_lock.reader_id = threading.get_ident()
                return store.max_branch_id("alice", 1)

            reader = executor.submit(query)
            try:
                assert signalled_lock.attempted.wait(5)
                assert not reader.done()
            finally:
                release.set()
            writer.result(timeout=5)
            assert reader.result(timeout=5) == 2
    finally:
        release.set()
        store.events = original_table
        store.close(update_status=False)


def test_cold_scan_paused_does_not_block_append_and_retries(tmp_path):
    store = SessionStore(tmp_path / "scan_retry.kohakutr", writer_lock=True)
    original_table = store.events
    entered, release = threading.Event(), threading.Event()

    class PausedScan:
        def __init__(self, table):
            self.table = table
            self.once = True
            self.scans = 0

        def __getattr__(self, name):
            return getattr(self.table, name)

        def keys(self, **kwargs):
            self.scans += 1
            return self.table.keys(**kwargs)

        def get_many(self, keys, default=None):
            rows = self.table.get_many(keys, default)
            if self.once:
                self.once = False
                entered.set()
                assert release.wait(5)
            return rows

        def __setitem__(self, key, value):
            self.table[key] = value

    paused_scan = PausedScan(original_table)
    store.events = paused_scan
    try:
        store.append_event("alice", "text", {}, turn_index=1, branch_id=1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            reader = pool.submit(store.max_branch_id, "alice", 1)
            try:
                assert entered.wait(5)
                store.append_event("alice", "text", {}, turn_index=1, branch_id=2)
                assert not reader.done()
            finally:
                release.set()
            assert reader.result(timeout=5) == 2
        assert paused_scan.scans == 2
    finally:
        release.set()
        store.events = original_table
        store.close(update_status=False)


def test_failed_native_write_after_write_invalidates_namespace(tmp_path):
    store = SessionStore(tmp_path / "fail_after.kohakutr", writer_lock=True)
    original_table = store.events

    class FailingTable:
        def __init__(self, table):
            self.table = table
            self.fail = False

        def __getattr__(self, name):
            return getattr(self.table, name)

        def __getitem__(self, key):
            return self.table[key]

        def __setitem__(self, key, value):
            self.table[key] = value
            if self.fail:
                raise OSError("simulated post-write failure")

    failing = FailingTable(original_table)
    store.events = failing
    try:
        store.append_event("alice", "text", {}, turn_index=1, branch_id=1)
        store.append_event("bob", "text", {}, turn_index=1, branch_id=10)
        assert store.max_branch_id("alice", 1) == 1
        assert store.max_branch_id("bob", 1) == 10
        assert "alice" in store._branch_maxima._agents
        assert "bob" in store._branch_maxima._agents

        failing.fail = True
        with pytest.raises(OSError, match="simulated post-write failure"):
            store.append_event("alice", "text", {}, turn_index=1, branch_id=2)

        assert "alice" not in store._branch_maxima._agents
        assert "bob" in store._branch_maxima._agents

        assert store.max_branch_id("alice", 1) == 2
        assert store.max_branch_id("bob", 1) == 10
    finally:
        store.events = original_table
        store.close(update_status=False)


def test_failed_native_write_before_write_invalidates_namespace_without_phantom(
    tmp_path,
):
    store = SessionStore(tmp_path / "fail_before.kohakutr", writer_lock=True)
    original_table = store.events

    class FailingBeforeTable:
        def __init__(self, table):
            self.table = table
            self.fail = False

        def __getattr__(self, name):
            return getattr(self.table, name)

        def __getitem__(self, key):
            return self.table[key]

        def __setitem__(self, key, value):
            if self.fail:
                raise OSError("simulated pre-write failure")
            self.table[key] = value

    failing = FailingBeforeTable(original_table)
    store.events = failing
    try:
        store.append_event("alice", "text", {}, turn_index=1, branch_id=1)
        store.append_event("bob", "text", {}, turn_index=1, branch_id=10)
        assert store.max_branch_id("alice", 1) == 1
        assert store.max_branch_id("bob", 1) == 10

        failing.fail = True
        with pytest.raises(OSError, match="simulated pre-write failure"):
            store.append_event("alice", "text", {}, turn_index=1, branch_id=2)

        assert "alice" not in store._branch_maxima._agents
        assert "bob" in store._branch_maxima._agents

        assert store.max_branch_id("alice", 1) == 1
        assert store.max_branch_id("bob", 1) == 10
    finally:
        store.events = original_table
        store.close(update_status=False)


def test_failed_write_during_cold_scan_forces_rebuild(tmp_path):
    store = SessionStore(tmp_path / "cold_failure.kohakutr", writer_lock=True)
    original_table = store.events
    entered, release = threading.Event(), threading.Event()

    class FailingScanTable:
        def __init__(self):
            self.once = True
            self.fail = False
            self.scans = 0

        def __getattr__(self, name):
            return getattr(original_table, name)

        def keys(self, **kwargs):
            self.scans += 1
            return original_table.keys(**kwargs)

        def __getitem__(self, key):
            return original_table[key]

        def get_many(self, keys, default=None):
            rows = original_table.get_many(keys, default)
            if self.once:
                self.once = False
                entered.set()
                assert release.wait(5)
            return rows

        def __setitem__(self, key, value):
            original_table[key] = value
            if self.fail:
                raise OSError("post-write failure during cold scan")

    table = FailingScanTable()
    store.events = table
    try:
        store.append_event("alice", "text", {}, turn_index=1, branch_id=1)
        with ThreadPoolExecutor(max_workers=1) as executor:
            reader = executor.submit(store.max_branch_id, "alice", 1)
            try:
                assert entered.wait(5)
                table.fail = True
                with pytest.raises(OSError, match="post-write failure"):
                    store.append_event("alice", "text", {}, turn_index=1, branch_id=2)
            finally:
                release.set()
            assert reader.result(timeout=5) == 2
        assert table.scans == 2
        assert store.max_branch_id("alice", 1) == 2
    finally:
        release.set()
        store.events = original_table
        store.close(update_status=False)


def test_locator_failure_does_not_prevent_branch_publication(tmp_path):
    store = SessionStore(tmp_path / "locator_fail.kohakutr", writer_lock=True)
    original_events = store.events
    original_record = store._event_keys.record
    try:
        store.append_event("alice", "text", {}, turn_index=1, branch_id=1)
        assert store.max_branch_id("alice", 1) == 1

        def broken_record(key, event_id):
            raise RuntimeError("locator index failure")

        store._event_keys.record = broken_record

        with pytest.raises(RuntimeError, match="locator index failure"):
            store.append_event("alice", "text", {}, turn_index=1, branch_id=2)

        assert store.max_branch_id("alice", 1) == 2
    finally:
        store._event_keys.record = original_record
        store.events = original_events
        store.close(update_status=False)


def test_subscriber_reenters_query_without_deadlock(tmp_path):
    store = SessionStore(tmp_path / "subscriber.kohakutr", writer_lock=True)
    original_events = store.events
    observed_max = []

    def subscriber(key, data):
        observed_max.append(store.max_branch_id("alice", 1))

    store.subscribe(subscriber)
    try:
        store.append_event("alice", "text", {}, turn_index=1, branch_id=1)
        assert observed_max == [1]
        store.append_event("alice", "text", {}, turn_index=1, branch_id=2)
        assert observed_max == [1, 2]
    finally:
        store.events = original_events
        store.close(update_status=False)
