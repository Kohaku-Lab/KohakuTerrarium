"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.creatures_plugins`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_plugins as plugins_mod,
)
from kohakuterrarium.terrarium.service import CreatureInfo


def _info(cid="cid", name="alice"):
    return CreatureInfo(
        creature_id=cid,
        name=name,
        graph_id="sess",
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
        list_returns=None,
        toggle_returns=None,
        toggle_raises=None,
        list_raises=None,
    ):
        self._creatures = creatures or [_info()]
        self._list_returns = list_returns or [{"name": "permgate"}]
        self._toggle_returns = toggle_returns or {"enabled": True}
        self._toggle_raises = toggle_raises
        self._list_raises = list_raises

    async def list_creatures(self):
        return tuple(self._creatures)

    async def list_plugins(self, cid):
        if self._list_raises is not None:
            raise self._list_raises
        return self._list_returns

    async def toggle_plugin(self, cid, name, enabled):
        if self._toggle_raises is not None:
            raise self._toggle_raises
        return self._toggle_returns


def _client(service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(plugins_mod.router)
    return TestClient(app)


class TestListPlugins:
    def test_success(self):
        svc = _FakeService()
        client = _client(svc)
        resp = client.get("/sess/creatures/alice/plugins")
        assert resp.status_code == 200
        assert resp.json() == [{"name": "permgate"}]

    def test_unknown_creature(self):
        svc = _FakeService(creatures=[])
        client = _client(svc)
        resp = client.get("/sess/creatures/ghost/plugins")
        assert resp.status_code == 404

    def test_creature_resolved_but_vanished_404s(self):
        # Creature is visible to list_creatures (resolution succeeds) but
        # the engine-level list_plugins raises KeyError — a removal race.
        # The route maps that to a clean 404, not a 500.
        svc = _FakeService(list_raises=KeyError("cid"))
        client = _client(svc)
        resp = client.get("/sess/creatures/alice/plugins")
        assert resp.status_code == 404


class TestTogglePlugin:
    def test_default_no_body(self):
        svc = _FakeService(toggle_returns={"enabled": True})
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/plugins/permgate/toggle")
        assert resp.status_code == 200
        assert resp.json() == {"enabled": True}

    def test_explicit_body(self):
        svc = _FakeService(toggle_returns={"enabled": False})
        client = _client(svc)
        resp = client.post(
            "/sess/creatures/alice/plugins/permgate/toggle",
            json={"enabled": False},
        )
        assert resp.status_code == 200

    def test_value_error_returns_404(self):
        svc = _FakeService(toggle_raises=ValueError("plugin not registered"))
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/plugins/ghost/toggle")
        assert resp.status_code == 404

    def test_unknown_creature(self):
        svc = _FakeService(creatures=[])
        client = _client(svc)
        resp = client.post("/sess/creatures/ghost/plugins/p/toggle")
        assert resp.status_code == 404

    def test_creature_resolved_but_vanished_404s(self):
        # toggle_plugin raises KeyError after a successful name resolution
        # → 404 (removal race), not a 500.
        svc = _FakeService(toggle_raises=KeyError("cid"))
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/plugins/permgate/toggle")
        assert resp.status_code == 404
