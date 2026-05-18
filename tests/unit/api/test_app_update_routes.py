"""HTTP surface for ``/api/app/*`` (06b)."""

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
        assert body["feed"]["kind"] == "github_releases"
        assert body["channel"] == "stable"
        assert body["update"]["mode"] == "notify-on-launch"

    def test_put_persists(self, client):
        resp = client.put(
            "/api/app/settings",
            json={
                "feed": {"kind": "github_releases", "repo": "x/y"},
                "channel": "beta",
                "pinned_version": "1.5.0",
                "update": {
                    "mode": "manual",
                    "check-cache-hours": 12,
                    "keep-versions": 5,
                },
            },
        )
        assert resp.status_code == 200
        echoed = resp.json()
        assert echoed["channel"] == "beta"
        assert echoed["pinned_version"] == "1.5.0"
        assert echoed["feed"]["repo"] == "x/y"

        body = client.get("/api/app/settings").json()
        assert body["update"]["mode"] == "manual"
        assert body["update"]["check-cache-hours"] == 12

    def test_invalid_payload_coerces_silently(self, client):
        # The new endpoint runs coercion rather than HTTP 400 — invalid
        # fields snap back to defaults so the UI never gets stuck on a
        # rejected save.
        resp = client.put(
            "/api/app/settings",
            json={"channel": "experimental", "update": {"mode": "weekly"}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["channel"] == "stable"
        assert body["update"]["mode"] == "notify-on-launch"


class TestState:
    def test_state_includes_install_metadata(self, client):
        resp = client.get("/api/app/state")
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "active",
            "installed",
            "settings",
            "launcher_install",
            "platform",
            "py_abi",
        ):
            assert key in body


class TestRejectionPaths:
    def test_lab_client_blocks_all_routes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        app = FastAPI()
        app.state.lab_mode = "lab-client"
        app.include_router(_r.router, prefix="/api/app")
        c = TestClient(app)
        for path in ("/api/app/settings", "/api/app/state"):
            assert c.get(path).status_code == 404

    def test_update_refuses_outside_launcher(self, client):
        # The default test environment has no active pointer — the
        # update / rollback routes must refuse with 409 so the UI
        # surfaces the "use kt self-update from terminal" hint.
        assert client.post("/api/app/update").status_code == 409
        assert client.post("/api/app/rollback").status_code == 409
