"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.creatures_ctl`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import creatures_ctl as ctl_mod
from kohakuterrarium.terrarium.service import CreatureInfo


def _info(cid="cid-1", name="alice") -> CreatureInfo:
    return CreatureInfo(
        creature_id=cid,
        name=name,
        graph_id="g",
        is_running=True,
        is_privileged=False,
        parent_creature_id=None,
        listen_channels=(),
        send_channels=(),
    )


class _FakeService:
    def __init__(
        self,
        *,
        creatures=None,
        interrupt_raises=None,
        stop_returns=True,
        list_jobs_raises=None,
        stop_job_raises=None,
        promote_raises=None,
    ):
        self._creatures = creatures or [_info()]
        self._interrupt_raises = interrupt_raises
        self._stop_returns = stop_returns
        self._list_jobs_raises = list_jobs_raises
        self._stop_job_raises = stop_job_raises
        self._promote_raises = promote_raises
        self.calls: list[tuple] = []

    async def list_creatures(self):
        return tuple(self._creatures)

    async def interrupt(self, cid):
        self.calls.append(("interrupt", cid))
        if self._interrupt_raises is not None:
            raise self._interrupt_raises

    async def list_jobs(self, cid):
        self.calls.append(("list_jobs", cid))
        if self._list_jobs_raises is not None:
            raise self._list_jobs_raises
        return [{"id": "j1"}]

    async def stop_job(self, cid, jid):
        self.calls.append(("stop_job", cid, jid))
        if self._stop_job_raises is not None:
            raise self._stop_job_raises
        return self._stop_returns

    async def promote_job(self, cid, jid):
        self.calls.append(("promote_job", cid, jid))
        if self._promote_raises is not None:
            raise self._promote_raises
        return True


def _client(service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(ctl_mod.router)
    return TestClient(app)


class TestInterrupt:
    def test_success(self):
        svc = _FakeService()
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/interrupt")
        assert resp.status_code == 200
        assert resp.json() == {"status": "interrupted"}

    def test_unknown_creature(self):
        svc = _FakeService()
        client = _client(svc)
        resp = client.post("/sess/creatures/ghost/interrupt")
        assert resp.status_code == 404

    def test_creature_resolved_but_vanished_404s(self):
        # Visible to list_creatures but the engine op raises KeyError
        # (removal race) → 404, not a 500.
        svc = _FakeService(interrupt_raises=KeyError("cid"))
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/interrupt")
        assert resp.status_code == 404


class TestListJobs:
    def test_success(self):
        svc = _FakeService()
        client = _client(svc)
        resp = client.get("/sess/creatures/alice/jobs")
        assert resp.status_code == 200
        assert resp.json() == [{"id": "j1"}]

    def test_creature_resolved_but_vanished_404s(self):
        svc = _FakeService(list_jobs_raises=KeyError("cid"))
        client = _client(svc)
        resp = client.get("/sess/creatures/alice/jobs")
        assert resp.status_code == 404


class TestStopJob:
    def test_success(self):
        svc = _FakeService(stop_returns=True)
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/tasks/j1/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_not_found(self):
        svc = _FakeService(stop_returns=False)
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/tasks/jX/stop")
        assert resp.status_code == 404

    def test_creature_resolved_but_vanished_404s(self):
        svc = _FakeService(stop_job_raises=KeyError("cid"))
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/tasks/j1/stop")
        assert resp.status_code == 404


class TestPromote:
    def test_success(self):
        svc = _FakeService()
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/promote/j1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "promoted"

    def test_creature_resolved_but_vanished_404s(self):
        svc = _FakeService(promote_raises=KeyError("cid"))
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/promote/j1")
        assert resp.status_code == 404
