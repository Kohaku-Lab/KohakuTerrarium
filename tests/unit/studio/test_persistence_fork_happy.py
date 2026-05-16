"""Happy-path tests for studio.persistence.fork.fork_session_handler."""

import pytest
from fastapi import HTTPException

from kohakuterrarium.studio.persistence import fork as fork_mod
from kohakuterrarium.session.errors import ForkNotStableError
from kohakuterrarium.session.store import SessionStore


def _build_store_with_event(tmp_path):
    """Build a real store + return the path and an event_id."""
    path = tmp_path / "parent.kohakutr"
    store = SessionStore(str(path))
    store.init_meta("p", "agent", "/p", "/w", ["alice"])
    store.append_event("alice", "user_message", {"content": "hi"})
    store.flush()
    # Find the event_id we just appended.
    events = list(store.get_all_events())
    eid = events[0][1]["event_id"]
    store.close()
    return path, eid


class TestForkSessionHandlerHappyPaths:
    async def test_success_no_mutation(self, monkeypatch, tmp_path):
        path, eid = _build_store_with_event(tmp_path)

        # Stub SessionStore.fork to avoid integrity preconditions.
        class _FakeChildStore:
            def __init__(self):
                self.session_id = "child-sid"
                self.path = str(tmp_path / "parent-fork1.kohakutr")

            def close(self, update_status=False):
                pass

        def _fake_fork(self, target, *, at_event_id, mutate, name):
            return _FakeChildStore()

        monkeypatch.setattr(SessionStore, "fork", _fake_fork)
        out = await fork_mod.fork_session_handler(
            path,
            at_event_id=eid,
            mutate_kind=None,
            mutate_args=None,
            name=None,
        )
        assert out["session_id"] == "child-sid"
        assert out["fork_point"] == eid

    async def test_success_with_mutation(self, monkeypatch, tmp_path):
        path, eid = _build_store_with_event(tmp_path)

        class _FakeChildStore:
            def __init__(self):
                self.session_id = "child-sid"
                self.path = str(tmp_path / "parent-edit.kohakutr")

            def close(self, update_status=False):
                pass

        def _fake_fork(self, target, *, at_event_id, mutate, name):
            return _FakeChildStore()

        monkeypatch.setattr(SessionStore, "fork", _fake_fork)
        out = await fork_mod.fork_session_handler(
            path,
            at_event_id=eid,
            mutate_kind="edit_user_message",
            mutate_args={"content": "new"},
            name="edit",
        )
        assert out["session_id"] == "child-sid"

    async def test_target_already_exists(self, monkeypatch, tmp_path):
        path, eid = _build_store_with_event(tmp_path)
        # Pre-create the target path so fork_target_path's result hits.
        existing = fork_mod.fork_target_path(path, "existing")
        existing.write_text("x")
        with pytest.raises(HTTPException) as exc:
            await fork_mod.fork_session_handler(
                path,
                at_event_id=eid,
                mutate_kind=None,
                mutate_args=None,
                name="existing",
            )
        assert exc.value.status_code == 409

    async def test_fork_not_stable_409(self, monkeypatch, tmp_path):
        path, eid = _build_store_with_event(tmp_path)

        def _fake_fork(self, target, **kw):
            raise ForkNotStableError("not stable")

        monkeypatch.setattr(SessionStore, "fork", _fake_fork)
        with pytest.raises(HTTPException) as exc:
            await fork_mod.fork_session_handler(
                path,
                at_event_id=eid,
                mutate_kind=None,
                mutate_args=None,
                name=None,
            )
        assert exc.value.status_code == 409

    async def test_fork_value_error_400(self, monkeypatch, tmp_path):
        path, eid = _build_store_with_event(tmp_path)

        def _fake_fork(self, target, **kw):
            raise ValueError("bad fork")

        monkeypatch.setattr(SessionStore, "fork", _fake_fork)
        with pytest.raises(HTTPException) as exc:
            await fork_mod.fork_session_handler(
                path,
                at_event_id=eid,
                mutate_kind=None,
                mutate_args=None,
                name=None,
            )
        assert exc.value.status_code == 400

    async def test_fork_unknown_exception_500(self, monkeypatch, tmp_path):
        path, eid = _build_store_with_event(tmp_path)

        def _fake_fork(self, target, **kw):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(SessionStore, "fork", _fake_fork)
        with pytest.raises(HTTPException) as exc:
            await fork_mod.fork_session_handler(
                path,
                at_event_id=eid,
                mutate_kind=None,
                mutate_args=None,
                name=None,
            )
        assert exc.value.status_code == 500
