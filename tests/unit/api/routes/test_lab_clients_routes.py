"""``/api/lab/clients/*`` + ``/api/lab/pairing-tokens/*`` route tests.

Uses a fake host engine so we can assert disconnect / blocklist
behaviour without spinning up a real WebSocket transport.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes import lab_clients as lc_mod


class _FakeClient:
    def __init__(self, node_id):
        self.node_id = node_id


class _FakeHost:
    def __init__(self):
        self._clients = {}
        self._config = SimpleNamespace(token="initial-token")
        self.active_token = "initial-token"
        self.blocked = set()
        self.disconnect_calls = []

    def set_token(self, token):
        self._config.token = token
        self.active_token = token

    def block_client_id(self, node_id):
        self.blocked.add(node_id)

    def unblock_client_id(self, node_id):
        self.blocked.discard(node_id)

    def blocked_clients(self):
        return set(self.blocked)

    async def _disconnect_client(self, client, *, reason):
        self.disconnect_calls.append((client.node_id, reason))
        self._clients.pop(client.node_id, None)


@pytest.fixture
def app():
    host = _FakeHost()
    host._clients["worker-1"] = _FakeClient("worker-1")
    host._clients["worker-2"] = _FakeClient("worker-2")
    app = FastAPI()
    app.state.lab_mode = "lab-host"
    app.state.lab_host_engine = host
    app.state.lab_token = "initial-token"
    app.include_router(lc_mod.router, prefix="/api/lab")
    return app, host


@pytest.fixture
def client(app):
    app_, _ = app
    return TestClient(app_)


class TestModeGuard:
    def test_standalone_returns_404(self):
        app = FastAPI()
        app.state.lab_mode = "standalone"
        app.include_router(lc_mod.router, prefix="/api/lab")
        c = TestClient(app)
        assert c.post("/api/lab/clients/x/disconnect").status_code == 404
        assert c.post("/api/lab/pairing-tokens/rotate").status_code == 404


class TestDisconnect:
    def test_disconnect_unknown_404(self, client):
        resp = client.post("/api/lab/clients/nosuch/disconnect")
        assert resp.status_code == 404

    def test_disconnect_evicts(self, app, client):
        _, host = app
        resp = client.post("/api/lab/clients/worker-1/disconnect")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert host.disconnect_calls == [("worker-1", "operator-disconnect")]
        assert "worker-1" not in host._clients


class TestBlock:
    def test_block_adds_to_blocklist_and_evicts(self, app, client):
        _, host = app
        resp = client.post("/api/lab/clients/worker-2/block", json={"reason": "noisy"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["reason"] == "noisy"
        assert "worker-2" in app[0].state.lab_blocklist
        assert "worker-2" in host.blocked
        assert host.disconnect_calls == [("worker-2", "operator-block")]

    def test_unblock(self, app, client):
        _, host = app
        client.post("/api/lab/clients/worker-2/block", json={"reason": ""})
        resp = client.delete("/api/lab/clients/blocklist/worker-2")
        assert resp.status_code == 200
        assert "worker-2" not in app[0].state.lab_blocklist
        assert "worker-2" not in host.blocked

    def test_list_blocked(self, app, client):
        client.post("/api/lab/clients/worker-1/block", json={"reason": ""})
        client.post("/api/lab/clients/worker-2/block", json={"reason": ""})
        resp = client.get("/api/lab/clients/blocklist")
        assert resp.status_code == 200
        assert sorted(resp.json()["blocked"]) == ["worker-1", "worker-2"]


class TestRotate:
    def test_rotate_changes_token(self, app, client):
        app_, host = app
        before = app_.state.lab_token
        resp = client.post("/api/lab/pairing-tokens/rotate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["token"] != before
        assert app_.state.lab_token == body["token"]
        assert host._config.token == body["token"]
        assert host.active_token == body["token"]
