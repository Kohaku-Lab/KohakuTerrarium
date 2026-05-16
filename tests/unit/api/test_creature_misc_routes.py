"""Unit tests for the smaller sessions_v2 creature routes
(model / command / memory)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_command as cmd_mod,
)
from kohakuterrarium.api.routes.sessions_v2 import (
    creatures_model as model_mod,
)
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
        switch_raises=None,
        execute_raises=None,
        switch_returns="claude",
        execute_returns=None,
    ):
        self._creatures = creatures or [_info()]
        self._switch_raises = switch_raises
        self._execute_raises = execute_raises
        self._switch_returns = switch_returns
        self._execute_returns = execute_returns or {"output": "ok"}

    async def list_creatures(self):
        return tuple(self._creatures)

    async def switch_model(self, cid, model):
        if self._switch_raises is not None:
            raise self._switch_raises
        return self._switch_returns

    async def execute_command(self, cid, command, args):
        if self._execute_raises is not None:
            raise self._execute_raises
        return self._execute_returns


def _client(router, service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(router)
    return TestClient(app)


# ── switch_model ───────────────────────────────────────────────


class TestSwitchModelRoute:
    def test_success(self):
        svc = _FakeService(switch_returns="claude-opus")
        client = _client(model_mod.router, svc)
        resp = client.post("/sess/creatures/alice/model", json={"model": "claude-opus"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "switched", "model": "claude-opus"}

    def test_unknown_creature(self):
        svc = _FakeService(creatures=[])
        client = _client(model_mod.router, svc)
        resp = client.post("/sess/creatures/ghost/model", json={"model": "x"})
        assert resp.status_code == 404

    def test_value_error(self):
        svc = _FakeService(switch_raises=ValueError("bad model"))
        client = _client(model_mod.router, svc)
        resp = client.post("/sess/creatures/alice/model", json={"model": "x"})
        assert resp.status_code == 400

    def test_key_error_returns_404(self):
        svc = _FakeService(switch_raises=KeyError("not found"))
        client = _client(model_mod.router, svc)
        resp = client.post("/sess/creatures/alice/model", json={"model": "x"})
        assert resp.status_code == 404


# ── execute_command ────────────────────────────────────────────


class TestExecuteCommandRoute:
    def test_success(self):
        svc = _FakeService(execute_returns={"output": "status: ok"})
        client = _client(cmd_mod.router, svc)
        resp = client.post(
            "/sess/creatures/alice/command",
            json={"command": "status", "args": ""},
        )
        assert resp.status_code == 200
        assert resp.json() == {"output": "status: ok"}

    def test_unknown_creature(self):
        svc = _FakeService(creatures=[])
        client = _client(cmd_mod.router, svc)
        resp = client.post(
            "/sess/creatures/ghost/command",
            json={"command": "status"},
        )
        assert resp.status_code == 404

    def test_value_error(self):
        svc = _FakeService(execute_raises=ValueError("Unknown command"))
        client = _client(cmd_mod.router, svc)
        resp = client.post(
            "/sess/creatures/alice/command",
            json={"command": "garbage"},
        )
        assert resp.status_code == 400

    def test_key_error_returns_404(self):
        svc = _FakeService(execute_raises=KeyError("not found"))
        client = _client(cmd_mod.router, svc)
        resp = client.post(
            "/sess/creatures/alice/command",
            json={"command": "x"},
        )
        assert resp.status_code == 404
