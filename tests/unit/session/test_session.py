"""Unit tests for :mod:`kohakuterrarium.session.session`."""

from pathlib import Path

import pytest

from kohakuterrarium.session.errors import NotAttachedError
from kohakuterrarium.session.session import Session, _derive_fork_path
from kohakuterrarium.session.store import SessionStore

# ── _derive_fork_path ─────────────────────────────────────────────


class TestDeriveForkPath:
    def test_bare_path(self, tmp_path):
        parent = str(tmp_path / "alice.kohakutr")
        child = _derive_fork_path(parent, name="abc")
        assert child.parent == tmp_path
        # v2 suffix since FORMAT_VERSION = 2.
        assert "alice-abc.kohakutr" in child.name
        assert child.name.endswith(".v2")

    def test_v2_parent_path(self, tmp_path):
        parent = str(tmp_path / "alice.kohakutr.v2")
        child = _derive_fork_path(parent, name="branch")
        # Strip .kohakutr+.v2 base, then re-append the fork tag.
        assert "alice-branch.kohakutr" in child.name

    def test_random_name_when_unset(self, tmp_path):
        parent = str(tmp_path / "alice.kohakutr")
        child = _derive_fork_path(parent, name=None)
        # Random short uuid suffix.
        assert "alice-fork-" in child.name

    def test_parent_without_kohakutr_suffix(self, tmp_path):
        parent = str(tmp_path / "notakohakutrfile.txt")
        child = _derive_fork_path(parent, name="x")
        # Falls back to using parent.stem.
        assert "notakohakutrfile-x" in child.name


# ── Session ───────────────────────────────────────────────────────


def _make_session(tmp_path, name="s.kohakutr") -> Session:
    store = SessionStore(str(tmp_path / name))
    return Session(store, agent=None)


class TestSessionConstruction:
    def test_basic(self, tmp_path):
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            sess = Session(store, agent=None)
            # The exact store and agent passed in are held.
            assert sess.store is store
            assert sess.agent is None
            # Default name == store.session_id ("s" from the filename).
            assert sess.name == store.session_id == "s"
        finally:
            store.close()

    def test_custom_name(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            sess = Session(store, name="my-sess")
            assert sess.name == "my-sess"
        finally:
            store.close()

    def test_path_property(self, tmp_path):
        sess = _make_session(tmp_path)
        try:
            assert sess.path == sess.store.path
        finally:
            sess.store.close()


# ── pending_job_ids ───────────────────────────────────────────────


class _FakeJob:
    def __init__(self, call_id=None, job_id=None):
        if call_id is not None:
            self.call_id = call_id
        if job_id is not None:
            self.job_id = job_id


class _FakeExecutor:
    def __init__(self, jobs):
        self._jobs = jobs

    def list_pending_jobs(self):
        return list(self._jobs)


class _FakeBrokenExecutor:
    def list_pending_jobs(self):
        raise RuntimeError("boom")


class _FakeAgent:
    def __init__(self, executor=None):
        self.executor = executor


class TestPendingJobIds:
    def test_no_agent_returns_empty(self, tmp_path):
        sess = _make_session(tmp_path)
        try:
            assert sess._pending_job_ids() == set()
        finally:
            sess.store.close()

    def test_no_executor_returns_empty(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            sess = Session(store, agent=_FakeAgent(executor=None))
            assert sess._pending_job_ids() == set()
        finally:
            store.close()

    def test_collects_call_ids(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            jobs = [_FakeJob(call_id="c1"), _FakeJob(call_id="c2")]
            sess = Session(store, agent=_FakeAgent(executor=_FakeExecutor(jobs)))
            assert sess._pending_job_ids() == {"c1", "c2"}
        finally:
            store.close()

    def test_collects_job_ids(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            jobs = [_FakeJob(job_id="j1")]
            sess = Session(store, agent=_FakeAgent(executor=_FakeExecutor(jobs)))
            assert sess._pending_job_ids() == {"j1"}
        finally:
            store.close()

    def test_dict_job(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            jobs = [{"call_id": "c1"}, {"job_id": "j2"}]
            sess = Session(store, agent=_FakeAgent(executor=_FakeExecutor(jobs)))
            assert sess._pending_job_ids() == {"c1", "j2"}
        finally:
            store.close()

    def test_executor_error_returns_empty(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            sess = Session(store, agent=_FakeAgent(executor=_FakeBrokenExecutor()))
            assert sess._pending_job_ids() == set()
        finally:
            store.close()

    def test_no_list_pending_jobs_returns_empty(self, tmp_path):
        store = SessionStore(str(tmp_path / "x.kohakutr"))
        try:
            # Executor has no list_pending_jobs method.
            sess = Session(store, agent=_FakeAgent(executor=object()))
            assert sess._pending_job_ids() == set()
        finally:
            store.close()


# ── detach_agent ──────────────────────────────────────────────────


class TestDetachAgent:
    def test_not_attached_raises(self, tmp_path):
        sess = _make_session(tmp_path)
        try:
            with pytest.raises(NotAttachedError):
                sess.detach_agent(_FakeAgent())
        finally:
            sess.store.close()

    def test_wrong_session_raises(self, tmp_path):
        sess = _make_session(tmp_path, "a.kohakutr")
        sess2 = _make_session(tmp_path, "b.kohakutr")
        try:
            agent = _FakeAgent()
            # Mimic the attachment-state attribute pointing to a
            # different session.
            setattr(agent, "_wave_f_attach_state", {"session": sess2})
            with pytest.raises(NotAttachedError):
                sess.detach_agent(agent)
        finally:
            sess.store.close()
            sess2.store.close()


# ── fork ──────────────────────────────────────────────────────────


class TestSessionFork:
    async def test_basic_fork(self, tmp_path):
        sess = _make_session(tmp_path, "alice.kohakutr")
        try:
            # Seed an event so fork has something to clone.
            sess.store.init_meta("sess", "agent", "/p", "/w", ["alice"])
            sess.store.append_event("alice", "user_message", {"content": "hi"})
            sess.store.flush()
            child = await sess.fork(at_event_id=1, name="branch-x")
            try:
                # Child path is a sibling of the parent, named for the
                # fork tag.
                assert Path(child.path).parent == tmp_path
                assert "alice-branch-x" in Path(child.path).name
                # Same agent set in cloned meta.
                child_meta = child.store.load_meta()
                assert "alice" in child_meta.get("agents", [])
                # The seeded event was actually cloned into the child.
                evts = child.store.get_events("alice")
                assert len(evts) == 1
                assert evts[0]["content"] == "hi"
                # Lineage records the parent.
                assert child_meta["lineage"]["fork"]["parent_session_id"] == "sess"
            finally:
                child.store.close()
        finally:
            sess.store.close()
