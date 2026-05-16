"""Unit tests for :mod:`kohakuterrarium.laboratory.adapters._worker_session`."""

from pathlib import Path

import pytest

from kohakuterrarium.laboratory.adapters._worker_session import (
    DEFAULT_WORKER_SESSION_DIR,
    WorkerSessionAttacher,
)


class _FakeAgent:
    def __init__(self):
        self.attached = []

    def attach_session_store(self, store):
        self.attached.append(store)


class _FakeCreature:
    def __init__(self, cid="c1", gid="g1"):
        self.creature_id = cid
        self.graph_id = gid
        self.agent = _FakeAgent()


class _FakeEngine:
    def __init__(self, creatures=None):
        self._creatures = creatures or {}
        self._session_stores = {}

    def get_creature(self, cid):
        if cid not in self._creatures:
            raise KeyError(cid)
        return self._creatures[cid]


class _FakeNotifier:
    """Stand-in for LabNotifier — SessionEventTee only calls .notify()."""

    def __init__(self):
        self.notified = []

    def notify(self, *args, **kw):
        self.notified.append((args, kw))


@pytest.fixture
def _attacher(tmp_path):
    engine = _FakeEngine()
    return WorkerSessionAttacher(engine, _FakeNotifier(), session_dir=tmp_path)


class TestConstructor:
    async def test_default_session_dir(self, tmp_path, monkeypatch):
        # Patch DEFAULT to a temp path so we don't touch ~/.kohakuterrarium.
        engine = _FakeEngine()
        att = WorkerSessionAttacher(
            engine, _FakeNotifier(), session_dir=tmp_path / "sess"
        )
        assert (tmp_path / "sess").is_dir()
        # No mutations expected on construction.
        assert att._graph_tees == {}
        assert att._graph_refs == {}

    async def test_default_dir_constant_is_path(self):
        assert isinstance(DEFAULT_WORKER_SESSION_DIR, Path)


class TestAttach:
    async def test_unknown_creature_silent(self, _attacher):
        # No creatures registered → attach is a no-op.
        _attacher.attach("ghost")
        assert _attacher._graph_tees == {}

    async def test_attaches_new_store(self, _attacher, tmp_path):
        c = _FakeCreature(cid="c1", gid="g1")
        _attacher._engine._creatures["c1"] = c
        _attacher.attach("c1")
        # Agent got a store attached.
        assert c.agent.attached
        # Tee was created.
        assert "g1" in _attacher._graph_tees
        assert "c1" in _attacher._graph_refs["g1"]
        # Store landed in the engine map.
        assert "g1" in _attacher._engine._session_stores
        # Cleanup
        _attacher.close_all()
        _attacher._engine._session_stores["g1"].close()

    async def test_reuses_engine_store(self, _attacher, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        existing = SessionStore(str(tmp_path / "exist.kohakutr"))
        try:
            c = _FakeCreature(cid="c1", gid="g1")
            _attacher._engine._creatures["c1"] = c
            _attacher._engine._session_stores["g1"] = existing
            _attacher.attach("c1")
            # Reused — no new store, same instance attached to agent.
            assert c.agent.attached[0] is existing
        finally:
            _attacher.close_all()
            existing.close()

    async def test_multiple_creatures_share_one_tee(self, _attacher):
        c1 = _FakeCreature(cid="c1", gid="g1")
        c2 = _FakeCreature(cid="c2", gid="g1")
        _attacher._engine._creatures["c1"] = c1
        _attacher._engine._creatures["c2"] = c2
        _attacher.attach("c1")
        _attacher.attach("c2")
        # Same Tee shared.
        assert len(_attacher._graph_tees) == 1
        assert _attacher._graph_refs["g1"] == {"c1", "c2"}
        _attacher.close_all()
        _attacher._engine._session_stores["g1"].close()


class TestDetach:
    async def test_detach_unknown_silent(self, _attacher):
        _attacher.detach("ghost")  # no-op
        assert _attacher._graph_tees == {}

    async def test_detach_keeps_tee_when_other_creatures_present(self, _attacher):
        c1 = _FakeCreature(cid="c1", gid="g1")
        c2 = _FakeCreature(cid="c2", gid="g1")
        _attacher._engine._creatures["c1"] = c1
        _attacher._engine._creatures["c2"] = c2
        _attacher.attach("c1")
        _attacher.attach("c2")
        _attacher.detach("c1")
        # Tee still alive — c2 is still attached.
        assert "g1" in _attacher._graph_tees
        assert "c2" in _attacher._graph_refs["g1"]
        _attacher.close_all()
        _attacher._engine._session_stores["g1"].close()

    async def test_detach_last_creature_tears_down_tee(self, _attacher):
        c1 = _FakeCreature(cid="c1", gid="g1")
        _attacher._engine._creatures["c1"] = c1
        _attacher.attach("c1")
        _attacher.detach("c1")
        assert "g1" not in _attacher._graph_tees
        assert "g1" not in _attacher._graph_refs
        _attacher._engine._session_stores["g1"].close()

    async def test_detach_skips_unrelated_graphs(self, _attacher):
        # Two creatures in two different graphs. Detaching the creature
        # in graph g2 must skip g1 entirely (the ``continue`` arm) and
        # only tear down g2 — g1's Tee and refs are untouched.
        c1 = _FakeCreature(cid="c1", gid="g1")
        c2 = _FakeCreature(cid="c2", gid="g2")
        _attacher._engine._creatures["c1"] = c1
        _attacher._engine._creatures["c2"] = c2
        _attacher.attach("c1")
        _attacher.attach("c2")
        _attacher.detach("c2")
        # g1 untouched; g2 torn down.
        assert "g1" in _attacher._graph_tees
        assert _attacher._graph_refs["g1"] == {"c1"}
        assert "g2" not in _attacher._graph_tees
        assert "g2" not in _attacher._graph_refs
        _attacher.close_all()
        _attacher._engine._session_stores["g1"].close()
        _attacher._engine._session_stores["g2"].close()


class TestCloseAll:
    async def test_close_all_idempotent(self, _attacher):
        c1 = _FakeCreature(cid="c1", gid="g1")
        _attacher._engine._creatures["c1"] = c1
        _attacher.attach("c1")
        _attacher.close_all()
        _attacher.close_all()
        assert _attacher._graph_tees == {}
        _attacher._engine._session_stores["g1"].close()
