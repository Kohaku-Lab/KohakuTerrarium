"""Unit tests for ``api/ext/routes.py`` — listing, enable toggle, asset tokens."""

from fastapi.testclient import TestClient

from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.auth.asset_token import verify_asset_token
from kohakuterrarium.api.auth.config import AuthConfig


def _client(auth: AuthConfig | None = None) -> TestClient:
    app = create_app()
    if auth is not None:
        app.state.auth_config = auth
    return TestClient(app)


def _by_name(resp):
    return {p["package"]: p for p in resp.json()["packages"]}


def test_listing_reports_contributions_enable_state_and_dev_url(
    ui_package, monkeypatch, reset_service
):
    ui_package("alpha")
    ui_package("broken", ui={"entry": "../x.js"})
    monkeypatch.setenv("KT_UI_DEV", "alpha=http://localhost:5174/src/index.js, junk")
    with _client() as client:
        listing = _by_name(client.get("/api/extensions/ui"))
    alpha = listing["alpha"]
    assert alpha["enabled"] is True and alpha["enabled_by_default"] is True
    assert alpha["choice"] is None
    assert alpha["dev_url"] == "http://localhost:5174/src/index.js"
    assert [c["owner"] for c in alpha["contributes"]] == [
        "package:alpha",
        "plugin:counter",
    ]
    assert alpha["has_handler"] and alpha["has_api"]
    assert listing["broken"]["enabled"] is False
    assert "without '..'" in listing["broken"]["error"]


def test_toggle_persists_and_null_restores_default(ui_package, reset_service):
    ui_package("alpha")
    with _client() as client:
        resp = client.put("/api/extensions/ui/alpha", json={"enabled": False})
        assert resp.status_code == 200
        assert _by_name(resp)["alpha"]["enabled"] is False
        assert _by_name(client.get("/api/extensions/ui"))["alpha"]["choice"] is False
        resp = client.put("/api/extensions/ui/alpha", json={"enabled": None})
        assert _by_name(resp)["alpha"]["enabled"] is True
        assert (
            client.put("/api/extensions/ui/nope", json={"enabled": True}).status_code
            == 404
        )


def test_toggle_requires_admin_token_when_configured(ui_package, reset_service):
    ui_package("alpha")
    with _client(AuthConfig(admin_token="ADMIN")) as client:
        assert (
            client.put("/api/extensions/ui/alpha", json={"enabled": False}).status_code
            == 401
        )
        resp = client.put(
            "/api/extensions/ui/alpha",
            json={"enabled": False},
            headers={"X-Admin-Token": "ADMIN"},
        )
        assert resp.status_code == 200


def test_multi_user_hosts_disable_by_default_and_need_a_user(ui_package, reset_service):
    ui_package("alpha")
    with _client(AuthConfig(multi_user="required")) as client:
        assert client.get("/api/extensions/ui").status_code == 401
        resp = client.post("/api/extensions/ui/asset-token", json={"package": "alpha"})
        assert resp.status_code == 401
        # Anonymous callers never reach the toggle under required L4 either.
        assert (
            client.put("/api/extensions/ui/alpha", json={"enabled": True}).status_code
            == 403
        )
    with _client(AuthConfig(multi_user="optional")) as client:
        alpha = _by_name(client.get("/api/extensions/ui"))["alpha"]
        assert alpha["enabled"] is False and alpha["enabled_by_default"] is False
        # Anonymous users are not admins.
        assert (
            client.put("/api/extensions/ui/alpha", json={"enabled": True}).status_code
            == 403
        )


def test_asset_token_is_signed_for_one_enabled_package(ui_package, reset_service):
    ui_package("alpha")
    with _client() as client:
        data = client.post(
            "/api/extensions/ui/asset-token", json={"package": "alpha"}
        ).json()
        secret = client.app.state.ui_asset_secret
        assert client.post("/api/extensions/ui/asset-token", json={}).status_code == 422
        resp = client.post("/api/extensions/ui/asset-token", json={"package": "ghost"})
        assert resp.status_code == 404
    assert verify_asset_token(secret, data["token"], "alpha")
    assert not verify_asset_token(secret, data["token"], "beta")
    assert data["expires_at"] > 0 and data["package"] == "alpha"


def test_toggle_invalidates_the_cached_choices(ui_package, reset_service):
    ui_package("alpha")
    with _client() as client:
        assert _by_name(client.get("/api/extensions/ui"))["alpha"]["enabled"] is True
        client.put("/api/extensions/ui/alpha", json={"enabled": False})
        token = client.post("/api/extensions/ui/asset-token", json={"package": "alpha"})
        assert token.status_code == 404
        assert client.get("/api/ext/alpha/hello").status_code == 404
