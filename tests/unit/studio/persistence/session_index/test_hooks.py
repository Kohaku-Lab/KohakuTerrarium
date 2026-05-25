"""Unit tests for ``session_index.hooks`` — every code path."""

from pathlib import Path

import pytest

from kohakuterrarium.session.store import SessionStore
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
            row2 = idx.get("alice.kohakutr")
            hook.detach()
        finally:
            s.close()
        # The second push captured the latest preview ("one" wins
        # because get_resumable_events returns the first user_input).
        assert row2["preview"] == "one"

    def test_event_flush_debounced_by_time(self, idx, tmp_path, monkeypatch):
        # Use n=999 so count never fires; advance monotonic clock
        # manually to trigger the time gate.
        s = _make_store(tmp_path, "alice")
        try:
            hook = SessionIndexHook(
                s, idx, flush_every_n_events=999, flush_every_seconds=0.001
            )
            # Default ``time.monotonic`` runs in real time; with our
            # tiny ``flush_every_seconds``, the next ``append_event``
            # almost certainly trips the gate.
            import time as _time

            _time.sleep(0.01)
            s.append_event("alice", "user_input", {"content": "after gate"})
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
