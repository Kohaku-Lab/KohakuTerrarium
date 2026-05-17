"""HTTP surface for ``/api/app/*``."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes import app_update as _r


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    app = FastAPI()
    app.state.lab_mode = "standalone"
    app.include_router(_r.router, prefix="/api/app")
    app.include_router(_r.ws_router)
    return TestClient(app)


class TestSettingsRoundTrip:
    def test_get_returns_defaults_on_fresh_install(self, client):
        resp = client.get("/api/app/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"]["kind"] == "pypi"
        assert body["update"]["mode"] == "notify-on-launch"

    def test_put_persists(self, client):
        resp = client.put(
            "/api/app/settings",
            json={"update": {"mode": "manual", "check-cache-hours": 12}},
        )
        assert resp.status_code == 200
        assert resp.json()["update"]["mode"] == "manual"
        # Round-trip via GET to confirm disk persistence.
        body = client.get("/api/app/settings").json()
        assert body["update"]["mode"] == "manual"
        assert body["update"]["check-cache-hours"] == 12

    def test_invalid_kind_rejected_400(self, client):
        resp = client.put("/api/app/settings", json={"source": {"kind": "bogus"}})
        assert resp.status_code == 400
        assert "invalid source.kind" in resp.json()["detail"]

    def test_invalid_mode_rejected_400(self, client):
        resp = client.put("/api/app/settings", json={"update": {"mode": "weekly"}})
        assert resp.status_code == 400


class TestUpdateStatus:
    def test_cached_status_no_probe(self, client):
        resp = client.get("/api/app/update-status")
        assert resp.status_code == 200
        body = resp.json()
        # No network probe by default — latest is null.
        assert body["latest-version"] is None
        assert "install-kind" in body
        assert "legacy-bundle" in body


class TestRejectionPaths:
    def test_lab_client_blocks_all_routes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        app = FastAPI()
        app.state.lab_mode = "lab-client"
        app.include_router(_r.router, prefix="/api/app")
        c = TestClient(app)
        for path in ("/api/app/settings", "/api/app/update-status"):
            assert c.get(path).status_code == 404

    def test_update_refuses_when_no_wrapper_marker(self, client):
        # The default test environment has no wrapper marker — the
        # update / rollback / reset-venv routes must refuse with 409
        # so the UI surfaces the "use kt self-update from terminal"
        # hint instead of silently producing a half-broken venv.
        assert client.post("/api/app/update").status_code == 409
        assert client.post("/api/app/rollback").status_code == 409
        assert client.post("/api/app/reset-venv").status_code == 409
