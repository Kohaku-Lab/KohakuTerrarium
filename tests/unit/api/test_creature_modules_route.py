"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.creatures_modules`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_modules as mods_mod,
)
from kohakuterrarium.terrarium.service import CreatureInfo


def _info(cid="cid", name="alice"):
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
    def __init__(self, **overrides):
        self._creatures = overrides.get("creatures", [_info()])
        self._list = overrides.get("list_returns", [{"name": "permgate"}])
        self._get = overrides.get("get_returns", {"name": "permgate"})
        self._set = overrides.get("set_returns", {"k": 1})
        self._toggle = overrides.get("toggle_returns", {"enabled": True})
        self._raise_on = overrides.get("raise_on", {})

    async def list_creatures(self):
        return tuple(self._creatures)

    async def list_modules(self, cid):
        if "list_modules" in self._raise_on:
            raise self._raise_on["list_modules"]
        return self._list

    async def get_module_options(self, cid, t, n):
        if "get_module_options" in self._raise_on:
            raise self._raise_on["get_module_options"]
        return self._get

    async def set_module_options(self, cid, t, n, v):
        if "set_module_options" in self._raise_on:
            raise self._raise_on["set_module_options"]
        return self._set

    async def toggle_module(self, cid, t, n):
        if "toggle_module" in self._raise_on:
            raise self._raise_on["toggle_module"]
        return self._toggle


def _client(service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(mods_mod.router)
    return TestClient(app)


class TestListModules:
    def test_success(self):
        client = _client(_FakeService())
        resp = client.get("/sess/creatures/alice/modules")
        assert resp.status_code == 200
        assert resp.json()["modules"] == [{"name": "permgate"}]

    def test_key_error(self):
        svc = _FakeService(raise_on={"list_modules": KeyError("no")})
        client = _client(svc)
        resp = client.get("/sess/creatures/alice/modules")
        assert resp.status_code == 404


class TestGetOptions:
    def test_success(self):
        client = _client(_FakeService(get_returns={"name": "permgate"}))
        resp = client.get("/sess/creatures/alice/modules/plugin/permgate/options")
        assert resp.status_code == 200
        # Route returns the service payload verbatim.
        assert resp.json() == {"name": "permgate"}

    def test_key_error(self):
        svc = _FakeService(raise_on={"get_module_options": KeyError("no")})
        client = _client(svc)
        resp = client.get("/sess/creatures/alice/modules/plugin/p/options")
        assert resp.status_code == 404

    def test_value_error(self):
        svc = _FakeService(raise_on={"get_module_options": ValueError("bad")})
        client = _client(svc)
        resp = client.get("/sess/creatures/alice/modules/plugin/p/options")
        assert resp.status_code == 400


class TestSetOptions:
    def test_success(self):
        client = _client(_FakeService(set_returns={"foo": 1}))
        resp = client.put(
            "/sess/creatures/alice/modules/plugin/permgate/options",
            json={"values": {"foo": 1}},
        )
        assert resp.status_code == 200
        # Route echoes status + type + name + the applied options.
        assert resp.json() == {
            "status": "saved",
            "type": "plugin",
            "name": "permgate",
            "options": {"foo": 1},
        }

    def test_key_error(self):
        svc = _FakeService(raise_on={"set_module_options": KeyError("no")})
        client = _client(svc)
        resp = client.put(
            "/sess/creatures/alice/modules/plugin/p/options",
            json={"values": {}},
        )
        assert resp.status_code == 404

    def test_value_error(self):
        svc = _FakeService(raise_on={"set_module_options": ValueError("bad")})
        client = _client(svc)
        resp = client.put(
            "/sess/creatures/alice/modules/plugin/p/options",
            json={"values": {}},
        )
        assert resp.status_code == 400


class TestToggleModule:
    def test_success(self):
        client = _client(_FakeService(toggle_returns={"enabled": False}))
        resp = client.post("/sess/creatures/alice/modules/plugin/permgate/toggle")
        assert resp.status_code == 200
        # Route returns the service's new-state payload verbatim.
        assert resp.json() == {"enabled": False}

    def test_key_error(self):
        svc = _FakeService(raise_on={"toggle_module": KeyError("no")})
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/modules/plugin/p/toggle")
        assert resp.status_code == 404

    def test_value_error(self):
        svc = _FakeService(raise_on={"toggle_module": ValueError("bad")})
        client = _client(svc)
        resp = client.post("/sess/creatures/alice/modules/plugin/p/toggle")
        assert resp.status_code == 400
