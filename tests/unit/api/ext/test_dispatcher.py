"""Unit tests for ``api/ext/dispatcher.py`` — package assets and raw routes."""

import importlib
import sys

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.packages.slots.ui import invalidate_ui_cache
from kohakuterrarium.studio.identity import ui_extensions


def _token(client: TestClient, package: str) -> str:
    resp = client.post("/api/extensions/ui/asset-token", json={"package": package})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_serves_assets_inside_ui_root_only(ui_package, reset_service):
    ui_package("alpha")
    with TestClient(create_app()) as client:
        token = _token(client, "alpha")
        resp = client.get(f"/api/ext/alpha/ui/{token}/index.js")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/javascript")
        assert "export default" in resp.text
        css = client.get(f"/api/ext/alpha/ui/{token}/sub/a.css")
        assert css.headers["content-type"].startswith("text/css")
        assert (
            client.get(f"/api/ext/alpha/ui/{token}/..%2Fsecret.txt").status_code == 404
        )
        assert client.get(f"/api/ext/alpha/ui/{token}/missing.js").status_code == 404
        assert client.post(f"/api/ext/alpha/ui/{token}/index.js").status_code == 405
        assert (
            client.get("/api/ext/nope/ui/x/index.js").json()["error"]
            == "extension_not_found"
        )


def test_asset_tokens_are_scoped_to_one_package(ui_package, reset_service):
    ui_package("alpha")
    ui_package("beta")
    with TestClient(create_app()) as client:
        alpha_token = _token(client, "alpha")
        assert (
            client.get(f"/api/ext/alpha/ui/{alpha_token}/index.js").status_code == 200
        )
        resp = client.get(f"/api/ext/beta/ui/{alpha_token}/index.js")
        assert resp.status_code == 403 and resp.json()["error"] == "asset_token_invalid"
        assert client.get("/api/ext/alpha/ui/forged.sig/index.js").status_code == 403
        assert (
            client.post(
                "/api/extensions/ui/asset-token", json={"package": "ghost"}
            ).status_code
            == 404
        )


def test_asset_token_admits_import_without_host_token(ui_package, reset_service):
    ui_package("alpha")
    ui_package("beta")
    app = create_app()
    app.state.auth_config = AuthConfig(host_token="HOST", loopback_bypass=False)
    host = {"X-KT-Host-Token": "HOST"}
    with TestClient(app) as client:
        token = client.post(
            "/api/extensions/ui/asset-token", json={"package": "alpha"}, headers=host
        ).json()["token"]
        assert client.get(f"/api/ext/alpha/ui/{token}/index.js").status_code == 200
        # The host gate stays shut for another package's assets with this token.
        assert client.get(f"/api/ext/beta/ui/{token}/index.js").status_code == 401
        assert client.get("/api/ext/alpha/hello").status_code == 401
        assert client.get("/api/ext/alpha/hello", headers=host).status_code == 200


def test_raw_api_shares_host_state_and_tags_the_package(ui_package, reset_service):
    ui_package("alpha")
    with TestClient(create_app()) as client:
        resp = client.get("/api/ext/alpha/hello")
        assert resp.status_code == 200
        assert resp.json() == {"package": "alpha", "creatures": 0}
        assert client.get("/api/ext/alpha/absent").status_code == 404


def test_raw_api_uses_the_per_user_engine_under_multi_user(ui_package, reset_service):
    ui_package("alpha")
    app = create_app()
    app.state.auth_config = AuthConfig(multi_user="optional")
    with TestClient(app) as client:
        ui_extensions.set_enabled("alpha", True)
        assert client.get("/api/ext/alpha/hello").status_code == 200
        assert None in set(app.state.engine_pool.live_user_ids())


def test_raw_api_requires_a_user_when_accounts_are_required(ui_package, l4_users):
    app, client_a, _b, _ua, _ub = l4_users
    ui_package("alpha")
    assert (
        client_a.put("/api/extensions/ui/alpha", json={"enabled": True}).status_code
        == 200
    )
    assert client_a.get("/api/ext/alpha/hello").status_code == 200
    with TestClient(app) as anonymous:
        resp = anonymous.get("/api/ext/alpha/hello")
        assert resp.status_code == 401 and resp.json()["error"] == "auth_required"


def test_disabled_package_serves_nothing(ui_package, reset_service):
    ui_package("alpha")
    ui_extensions.set_enabled("alpha", False)
    with TestClient(create_app()) as client:
        resp = client.post("/api/extensions/ui/asset-token", json={"package": "alpha"})
        assert resp.status_code == 404
        assert (
            client.get("/api/ext/alpha/ui/x.y/index.js").json()["error"]
            == "extension_disabled"
        )
        assert client.get("/api/ext/alpha/hello").status_code == 404
        with pytest.raises(WebSocketDisconnect) as info:
            with client.websocket_connect("/api/ext/alpha/ws"):
                pass
        assert info.value.code == 4404


def test_installing_another_package_keeps_existing_sub_apps(
    ui_package, packages_dir, reset_service
):
    module = ui_package("alpha")
    with TestClient(create_app()) as client:
        assert client.get("/api/ext/alpha/hello").status_code == 200
        ui_package("beta")
        assert client.get("/api/ext/beta/hello").status_code == 200
        assert client.get("/api/ext/alpha/hello").status_code == 200
        assert sys._kt_ui_loads.count(module) == 1


def test_broken_api_reports_500_and_rebuilds_after_manifest_change(
    ui_package, packages_dir, reset_service
):
    module = ui_package("alpha")
    source = packages_dir / "alpha" / f"{module}.py"
    original = source.read_text()
    source.write_text("raise ImportError('nope')\n")
    with TestClient(create_app()) as client:
        resp = client.get("/api/ext/alpha/hello")
        assert resp.status_code == 500 and "nope" in resp.json()["detail"]
        source.write_text(original)
        manifest = packages_dir / "alpha" / "kohaku.yaml"
        manifest.write_text(manifest.read_text().replace("0.3.0", "0.3.1"))
        invalidate_ui_cache()
        assert client.get("/api/ext/alpha/hello").status_code == 200
    importlib.invalidate_caches()


def test_package_without_api_has_no_raw_routes(ui_package, reset_service):
    ui_package("alpha", ui={"api": None})
    with TestClient(create_app()) as client:
        resp = client.get("/api/ext/alpha/hello")
        assert resp.status_code == 404 and resp.json()["error"] == "extension_api"
