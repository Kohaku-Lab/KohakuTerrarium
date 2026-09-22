"""Unit tests for ``session_index.hooks`` — every code path."""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index import hooks as hooks_mod
from kohakuterrarium.studio.persistence.session_index.hooks import (
    SessionIndexHook,
    push_index_update,
)
from kohakuterrarium.studio.persistence.session_index.store import SessionIndex


@pytest.fixture
def idx(tmp_path):
    side = tmp_path / ".kt-index.kvault"
    i = SessionIndex(side)
    try:
        yield i
    finally:
        i.close()


def _make_store(tmp_path: Path, name: str, agent: str = "alice") -> SessionStore:
    s = SessionStore(str(tmp_path / f"{name}.kohakutr"))
    s.init_meta(f"sid-{name}", "agent", "", "", [agent])
    s.flush()
    return s


# ── push_index_update ────────────────────────────────────────────


class TestPushIndexUpdate:
    def test_inserts_fresh_entry(self, idx, tmp_path):
        s = _make_store(tmp_path, "alice")
        try:
            entry = push_index_update(s, idx)
            assert entry is not None
            assert entry.name == "alice"
        finally:
            s.close()
        assert idx.list().total == 1

    def test_updates_existing_entry(self, idx, tmp_path):
        s = _make_store(tmp_path, "alice")
        try:
            push_index_update(s, idx)
            s.append_event("alice", "user_input", {"content": "fresh preview"})
            s.flush()
            push_index_update(s, idx)
        finally:
            s.close()
        row = idx.get("alice.kohakutr")
        assert row["preview"] == "fresh preview"
        assert idx.list().total == 1  # not duplicated

    def test_swallows_load_meta_exception(self, idx):
        # Hand in a fake store whose load_meta raises — function
        # returns None and logs at debug.
        class Boom:
            _path = "/tmp/nope.kohakutr"

            def load_meta(self):
                raise RuntimeError("meta corrupt")

        out = push_index_update(Boom(), idx)
        assert out is None


# ── SessionIndexHook ─────────────────────────────────────────────


class TestSessionIndexHook:
    def test_slow_push_cooldown_starts_after_completion(
        self, idx, tmp_path, monkeypatch
    ):
        now = 100.0
        monkeypatch.setattr(hooks_mod, "time", SimpleNamespace(monotonic=lambda: now))
        original_upsert = idx.upsert

        def slow_upsert(entry):
            nonlocal now
            original_upsert(entry)
            now += 6.0

        monkeypatch.setattr(idx, "upsert", slow_upsert)
        store = _make_store(tmp_path, "cooldown")
        hook = None
        try:
            hook = SessionIndexHook(store, idx, flush_every_n_events=999)
            store.append_event("alice", "user_input", {"content": "new input"})
            hook.detach()
            assert idx.get("cooldown.kohakutr")["preview"] == ""
        finally:
            if hook is not None:
                hook.detach()
            store.close()

    def test_busy_index_does_not_block_store_writes_and_final_flush_is_current(
        self, idx, tmp_path, monkeypatch
    ):
        entered = threading.Event()
        release = threading.Event()
        original_upsert = idx.upsert
        writes = []

        def gated_upsert(entry):
            entered.set()
            assert release.wait(5), "test did not release the index writer"
            original_upsert(entry)
            writes.append(entry)

        monkeypatch.setattr(idx, "upsert", gated_upsert)
        store = _make_store(tmp_path, "busy")
        hook = SessionIndexHook(
            store, idx, push_on_attach=False, flush_every_n_events=1
        )
        try:
            first = store.submit(
                store.append_event, "alice", "user_input", {"content": "first"}
            )
            assert entered.wait(2), "index refresh did not start"
            first.result(timeout=0.5)
            for _ in range(25):
                store.submit(
                    store.append_event, "alice", "text", {"content": "progress"}
                ).result(timeout=0.5)
            store.submit(store.update_status, "paused").result(timeout=0.5)
            release.set()
            hook.flush()
            hook.detach()
            row = idx.get("busy.kohakutr")
            assert row["preview"] == "first"
            assert row["status"] == "paused"
            assert len(writes) <= 3, "events during a pending refresh must coalesce"
        finally:
            release.set()
            hook.detach()
            store.close()

    def test_attach_pushes_initial_entry(self, idx, tmp_path):
        s = _make_store(tmp_path, "alice")
        try:
            hook = SessionIndexHook(s, idx)
            assert idx.list().total == 1
            hook.detach()
        finally:
            s.close()

    def test_attach_can_skip_initial_push(self, idx, tmp_path):
        s = _make_store(tmp_path, "alice")
        try:
            hook = SessionIndexHook(s, idx, push_on_attach=False)
            assert idx.list().total == 0
            hook.detach()
        finally:
            s.close()

    def test_event_flush_debounced_by_count(self, idx, tmp_path):
        # n=2 → push once, then again on the 2nd event after the
        # initial push.  (push_on_attach also calls flush, which
        # resets the counter — so the first append makes count=1,
        # second makes count=2 → triggers a push.)
        s = _make_store(tmp_path, "alice")
        try:
            hook = SessionIndexHook(
                s, idx, flush_every_n_events=2, flush_every_seconds=999
            )
            # Initial push counted as zero events.  Drop one event:
            # counter goes to 1 (no push).
            s.append_event("alice", "user_input", {"content": "one"})
            # Drop a second event: counter 2 → push.
            s.append_event("alice", "user_input", {"content": "two"})
            hook.detach()
            row2 = idx.get("alice.kohakutr")
        finally:
            s.close()
        # The second push captured the latest preview ("one" wins
        # because get_resumable_events returns the first user_input).
        assert row2["preview"] == "one"

    def test_event_flush_debounced_by_time(self, idx, tmp_path, monkeypatch):
        now = 100.0
        monkeypatch.setattr(hooks_mod, "time", SimpleNamespace(monotonic=lambda: now))
        s = _make_store(tmp_path, "alice")
        try:
            hook = SessionIndexHook(
                s, idx, flush_every_n_events=999, flush_every_seconds=5
            )
            now = 104.0
            s.append_event("alice", "user_input", {"content": "after gate"})
            assert idx.get("alice.kohakutr")["preview"] == ""
            now = 105.0
            s.append_event("alice", "text", {"content": "gate reached"})
            hook.detach()
        finally:
            s.close()
        row = idx.get("alice.kohakutr")
        assert row["preview"] == "after gate"

    def test_flush_pushes_immediately(self, idx, tmp_path):
        s = _make_store(tmp_path, "alice")
        try:
            hook = SessionIndexHook(
                s,
                idx,
                flush_every_n_events=999,
                flush_every_seconds=999,
                push_on_attach=False,
            )
            assert idx.list().total == 0
            hook.flush()
            assert idx.list().total == 1
            hook.detach()
        finally:
            s.close()

    def test_detach_stops_listening(self, idx, tmp_path):
        s = _make_store(tmp_path, "alice")
        try:
            hook = SessionIndexHook(
                s,
                idx,
                flush_every_n_events=1,
                flush_every_seconds=999,
                push_on_attach=True,
            )
            hook.detach()
            # After detach, events don't push.
            s.append_event("alice", "user_input", {"content": "ignored"})
            row = idx.get("alice.kohakutr")
            # Initial push captured no preview.
            assert row["preview"] == ""
        finally:
            s.close()

    def test_detach_is_idempotent(self, idx, tmp_path):
        s = _make_store(tmp_path, "alice")
        try:
            hook = SessionIndexHook(s, idx, push_on_attach=False)
            hook.detach()
            hook.detach()  # no raise
        finally:
            s.close()

    def test_context_manager_form(self, idx, tmp_path):
        s = _make_store(tmp_path, "alice")
        try:
            with SessionIndexHook(s, idx, push_on_attach=False) as hook:
                s.append_event("alice", "user_input", {"content": "ctx"})
                assert hook is not None
            # On exit, flush + detach run.  Entry is present.
            assert idx.list().total == 1
        finally:
            s.close()

    def test_attach_is_idempotent(self, idx, tmp_path):
        # Calling _attach twice via construction would double-subscribe
        # the callback.  The internal ``_attached`` flag prevents that.
        s = _make_store(tmp_path, "alice")
        try:
            hook = SessionIndexHook(s, idx, push_on_attach=False)
            hook._attach(push_on_attach=False)
            # Only one subscriber was registered.
            count = sum(1 for cb in s._event_subscribers if cb is hook._listener)
            assert count == 1
            hook.detach()
        finally:
            s.close()

    def test_detach_swallows_unsubscribe_failure(self, idx, tmp_path):
        s = _make_store(tmp_path, "alice")
        try:
            hook = SessionIndexHook(s, idx, push_on_attach=False)

            # Replace store's unsubscribe with one that raises.
            def boom(_cb):
                raise RuntimeError("unsubscribe fail")

            s.unsubscribe = boom  # monkey-patch instance method
            hook.detach()  # must not raise
        finally:
            s.close()


class TestSharedIndexWriter:
    def test_sessions_share_writer_and_detach_keeps_peers_working(
        self, idx, tmp_path, monkeypatch
    ):
        stores = []
        hooks = []
        writer_threads = set()
        upsert = idx.upsert

        def observed_upsert(entry):
            writer_threads.add(threading.current_thread())
            upsert(entry)

        monkeypatch.setattr(idx, "upsert", observed_upsert)
        try:
            for number in range(8):
                store = _make_store(tmp_path, f"session-{number}")
                stores.append(store)
                hooks.append(SessionIndexHook(store, idx, flush_every_n_events=1))
            assert idx.list().total == 8
            assert len(writer_threads) == 1
            hooks[0].detach()
            stores[0].close(update_status=False)
            for store, hook in zip(stores[1:], hooks[1:]):
                store.submit(
                    store.append_event,
                    "alice",
                    "user_input",
                    {"content": "still alive"},
                ).result(timeout=2)
                hook.flush()
                assert idx.get(Path(store.path).name)["preview"] == "still alive"
            assert len(writer_threads) == 1
        finally:
            for hook in hooks:
                hook.detach()
            for store in stores:
                store.close(update_status=False)
        idx.close()
        assert not any(thread.is_alive() for thread in writer_threads)

    def test_blocked_snapshot_does_not_delay_other_sessions(self, idx, tmp_path):
        slow = _make_store(tmp_path, "slow")
        ready = _make_store(tmp_path, "ready")
        hooks = [
            SessionIndexHook(store, idx, push_on_attach=False, flush_every_n_events=1)
            for store in (slow, ready)
        ]
        release = threading.Event()
        blocked = slow.submit(release.wait, 5)
        try:
            # Schedule a snapshot behind slow's blocked affinity work.
            slow.append_event("alice", "user_input", {"content": "slow snapshot"})
            ready.submit(
                ready.append_event, "alice", "user_input", {"content": "ready snapshot"}
            ).result(timeout=2)
            with ThreadPoolExecutor(max_workers=1) as caller:
                try:
                    caller.submit(hooks[1].flush).result(timeout=2)
                    assert idx.get("ready.kohakutr")["preview"] == "ready snapshot"
                    assert not blocked.done()
                finally:
                    release.set()
            hooks[0].flush()
            assert idx.get("slow.kohakutr")["preview"] == "slow snapshot"
        finally:
            release.set()
            for hook in hooks:
                hook.detach()
            slow.close(update_status=False)
            ready.close(update_status=False)


@pytest.mark.parametrize("stage", ["snapshot", "write"])
def test_failed_shared_refresh_finishes_and_can_retry(
    idx, tmp_path, monkeypatch, stage
):
    store = _make_store(tmp_path, "retry")
    hook = SessionIndexHook(store, idx, push_on_attach=False)
    owner, method = (store, "load_meta") if stage == "snapshot" else (idx, "upsert")
    original = getattr(owner, method)

    def failed(*args, **kwargs):
        raise OSError("injected storage failure")

    try:
        monkeypatch.setattr(owner, method, failed)
        hook.flush()
        assert idx.list().total == 0
        monkeypatch.setattr(owner, method, original)
        store.append_event("alice", "user_input", {"content": "retry succeeded"})
        hook.flush()
        assert idx.get("retry.kohakutr")["preview"] == "retry succeeded"
    finally:
        hook.detach()
        store.close(update_status=False)


def test_index_closed_before_snapshot_finishes_does_not_block_detach(idx, tmp_path):
    store = _make_store(tmp_path, "late")
    hook = SessionIndexHook(store, idx, push_on_attach=False, flush_every_n_events=1)
    release = threading.Event()
    store.submit(release.wait, 5)
    try:
        store.append_event("alice", "user_input", {"content": "retained event"})
        idx.close()
        release.set()
        hook.detach()
        assert store.get_events("alice")[0]["content"] == "retained event"
    finally:
        release.set()
        hook.detach()
        store.close(update_status=False)
