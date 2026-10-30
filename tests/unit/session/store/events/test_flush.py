"""Unit tests for :mod:`kohakuterrarium.session.store.events.flush`."""

import threading
from contextlib import closing

import pytest
from kohakuvault import KVault, TextVault

import kohakuterrarium.session.store.events.flush as flush_mod
from kohakuterrarium.session.store.events.flush import (
    EventFlushPipeline,
    insert_fts_rows,
)
from kohakuterrarium.session.vault_handles import dispose


class _Timers:
    """Timer factory whose timers fire only when the test says so."""

    def __init__(self):
        self.made = []

    def __call__(self, interval, callback):
        timer = _ManualTimer(interval, callback)
        self.made.append(timer)
        return timer

    def live(self):
        return [t for t in self.made if t.started and not t.cancelled]


class _ManualTimer:
    def __init__(self, interval, callback):
        self.interval, self.callback = interval, callback
        self.started = self.cancelled = False
        self.daemon = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        assert self.daemon and not self.cancelled
        self.callback()


class _Events:
    def __init__(self, fail=0):
        self.flushes, self.fail = 0, fail

    def flush_cache(self):
        if self.fail:
            self.fail -= 1
            raise OSError("flush failed")
        self.flushes += 1
        return 0


class _Fts:
    def __init__(self):
        self.rows, self.batches = [], []

    def insert_many(self, rows):
        self.batches.append(list(rows))
        self.rows.extend(rows)

    def insert(self, text, meta):
        self.rows.append((text, meta))


def _pipeline(events=None, fts=None, *, n=3, seconds=60.0, timers=None):
    timers = timers if timers is not None else _Timers()
    pipe = EventFlushPipeline(
        events or _Events(),
        fts,
        every_n=n,
        every_seconds=seconds,
        timer_factory=timers,
    )
    return pipe, timers


class TestInsertFtsRows:
    def test_batch_insert(self):
        fts = _Fts()
        insert_fts_rows(fts, [("a", {"i": 1}), ("b", {"i": 2})])
        insert_fts_rows(fts, [])
        assert fts.batches == [[("a", {"i": 1}), ("b", {"i": 2})]]

    def test_failed_batch_retries_per_row_and_logs_bad_rows(self, caplog):
        class Flaky(_Fts):
            def insert_many(self, rows):
                raise ValueError("batch failed")

            def insert(self, text, meta):
                if text == "bad":
                    raise ValueError("row failed")
                super().insert(text, meta)

        fts = Flaky()
        insert_fts_rows(fts, [("a", {}), ("bad", {}), ("c", {})])
        assert fts.rows == [("a", {}), ("c", {})]
        assert "FTS batch insert failed" in caplog.text
        assert "FTS indexing failed" in caplog.text

    def test_real_textvault(self, tmp_path):
        fts = TextVault(str(tmp_path / "f.db"), table="fts")
        fts.enable_auto_pack()
        try:
            insert_fts_rows(fts, [("needle in text", {"k": 1}), ("other", {"k": 2})])
            hits = fts.search("needle")
            assert [meta for _, _, meta in hits] == [{"k": 1}]
        finally:
            dispose(fts)


class TestGates:
    def test_n_gate_commits_fts_then_events(self):
        order = []

        class Events(_Events):
            def flush_cache(self):
                order.append("events")
                return super().flush_cache()

        class Fts(_Fts):
            def insert_many(self, rows):
                order.append("fts")
                super().insert_many(rows)

        events, fts = Events(), Fts()
        pipe, _ = _pipeline(events, fts, n=3)
        pipe.queue_fts("hello world", {"i": 0})
        pipe.appended()
        pipe.appended()
        assert events.flushes == 0 and pipe.unflushed == 2
        pipe.appended()
        assert order == ["fts", "events"]
        assert pipe.unflushed == 0 and fts.rows == [("hello world", {"i": 0})]

    def test_time_gate(self, monkeypatch):
        now = [100.0]
        monkeypatch.setattr(flush_mod.time, "monotonic", lambda: now[0])
        events = _Events()
        pipe, _ = _pipeline(events, n=0, seconds=5.0)
        pipe.appended()
        assert events.flushes == 0
        now[0] = 105.0
        pipe.appended()
        assert events.flushes == 1 and pipe.unflushed == 0

    def test_disabled_gates_wait_for_explicit_flush(self):
        events = _Events()
        pipe, timers = _pipeline(events, n=0, seconds=0)
        for _ in range(10):
            pipe.appended()
        assert events.flushes == 0 and timers.made == []
        pipe.flush()
        assert events.flushes == 1 and pipe.unflushed == 0

    def test_failed_gate_flush_is_logged_and_retried(self, caplog):
        events = _Events(fail=1)
        pipe, timers = _pipeline(events, n=1)
        pipe.appended()
        assert "Events flush failed" in caplog.text
        assert pipe.unflushed == 1 and len(timers.live()) == 1
        timers.live()[0].fire()
        assert events.flushes == 1 and pipe.unflushed == 0


class TestIdleTimer:
    def test_one_timer_flushes_idle_rows(self):
        events, fts = _Events(), _Fts()
        pipe, timers = _pipeline(events, fts, n=100, seconds=2.0)
        pipe.appended()
        pipe.appended()
        pipe.queue_fts("text row", {})
        assert len(timers.made) == 1 and timers.made[0].interval == 2.0
        timers.made[0].fire()
        assert events.flushes == 1 and fts.rows == [("text row", {})]
        pipe.appended()
        assert len(timers.made) == 2 and timers.made[1].started

    def test_fts_row_alone_arms_timer(self):
        fts = _Fts()
        pipe, timers = _pipeline(fts=fts, n=100, seconds=1.0)
        pipe.queue_fts("only text", {})
        timers.made[0].fire()
        assert fts.rows == [("only text", {})]

    def test_timer_with_nothing_pending_is_noop(self):
        events = _Events()
        pipe, timers = _pipeline(events, n=1, seconds=1.0)
        pipe.queue_fts("x" * 20, {})
        pipe.appended()
        timers.made[0].fire()
        assert events.flushes == 1

    def test_failed_idle_flush_rearms(self, caplog):
        events = _Events(fail=1)
        pipe, timers = _pipeline(events, n=100, seconds=1.0)
        pipe.appended()
        timers.made[0].fire()
        assert "Idle events flush failed" in caplog.text
        assert len(timers.made) == 2
        timers.made[1].fire()
        assert events.flushes == 1

    def test_flush_cancels_timer_and_propagates_failure(self):
        events = _Events(fail=1)
        pipe, timers = _pipeline(events, n=100, seconds=1.0)
        pipe.appended()
        with pytest.raises(OSError, match="flush failed"):
            pipe.flush()
        assert timers.made[0].cancelled

    def test_real_timer_flushes_without_appends(self, tmp_path):
        with closing(KVault(str(tmp_path / "e.db"), table="events")) as events:
            events.enable_cache()
            pipe = EventFlushPipeline(events, None, every_n=1000, every_seconds=0.05)
            done = threading.Event()
            real = events.flush_cache

            def flush_cache():
                result = real()
                done.set()
                return result

            events.flush_cache = flush_cache
            with pipe.lock:
                events["a:e0"] = b"row"
                pipe.appended()
            assert done.wait(5)
            assert pipe.unflushed == 0
            pipe.close()


class TestClose:
    def test_close_commits_and_stops_timers(self):
        events, fts = _Events(), _Fts()
        pipe, timers = _pipeline(events, fts, n=100, seconds=1.0)
        pipe.appended()
        pipe.queue_fts("closing text", {})
        pipe.close()
        assert events.flushes == 1 and fts.rows == [("closing text", {})]
        assert timers.made[0].cancelled
        pipe.queue_fts("late", {})
        pipe.appended()
        assert timers.live() == []
        # A timer that raced past cancel() still finds the pipeline closed.
        timers.made[0].cancelled = False
        timers.made[0].fire()
        assert events.flushes == 1

    def test_lock_is_reentrant(self):
        pipe, _ = _pipeline(n=1)
        with pipe.lock:
            pipe.appended()
            pipe.flush()
