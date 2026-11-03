"""Global module defaults use the existing administrative write gate."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.routes.identity.module_defaults import router
from kohakuterrarium.studio.identity.module_defaults import defaults_path, load_defaults
from kohakuterrarium.utils.file.lock import FileLock


def test_global_defaults_require_admin_without_affecting_reads():
    app = FastAPI()
    app.state.auth_config = AuthConfig(admin_token="test-admin")
    app.include_router(router, prefix="/api/settings")
    with TestClient(app) as client:
        assert client.get("/api/settings/module-defaults").status_code == 200
        url = "/api/settings/module-defaults/tool/web_search"
        body = {"values": {"fallback": "duckduckgo"}}
        denied = client.put(url, json=body)
        assert denied.status_code == 401
        assert denied.headers["X-Auth-Required"] == "admin"
        assert load_defaults() == {}
        saved = client.put(url, json=body, headers={"X-Admin-Token": "test-admin"})
        assert saved.status_code == 200
        assert load_defaults()["tool"]["web_search"]["options"] == {
            "fallback": "duckduckgo"
        }

        headers = {"X-Admin-Token": "test-admin"}
        before_invalid = defaults_path().read_bytes()
        invalid = client.put(
            "/api/settings/module-defaults/plugin/budget",
            json={"values": {"turn_budget": {"hard": "abc"}}},
            headers=headers,
        )
        assert invalid.status_code == 400
        assert "numeric" in invalid.json()["detail"]
        assert defaults_path().read_bytes() == before_invalid
        assert (
            client.put(
                url, json={"values": {"backend": "invalid"}}, headers=headers
            ).status_code
            == 400
        )
        assert (
            client.put(
                "/api/settings/module-defaults/tool/missing", json=body, headers=headers
            ).status_code
            == 404
        )
        with FileLock(defaults_path().with_suffix(".lock")):
            assert client.put(url, json=body, headers=headers).status_code == 409
        assert load_defaults()["tool"]["web_search"]["options"] == {
            "fallback": "duckduckgo"
        }
