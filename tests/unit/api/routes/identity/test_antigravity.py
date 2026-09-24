import time

import httpx
import pytest
from fastapi import FastAPI

from kohakuterrarium.api.auth.dependencies import verify_admin_token
from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.routes.identity import antigravity as routes
from kohakuterrarium.api.routes.identity import api_keys as key_routes
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.llm import antigravity_auth as auth
from kohakuterrarium.studio.identity import api_keys, llm_backends
from kohakuterrarium.cli.auth import login_cli


@pytest.mark.asyncio
async def test_host_status_offline_remote_rejected_and_mutations_admin_gated(
    monkeypatch,
):
    monkeypatch.setattr(
        auth,
        "read_sources",
        lambda: [auth.BorrowedCredential("never-show", time.time() + 3600, "test")],
    )
    app = FastAPI()
    app.include_router(routes.router, prefix="/settings")
    app.dependency_overrides[verify_admin_token] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.get("/settings/antigravity-status")
        assert result.status_code == 200
        assert result.json()["state"] == "ready"
        assert "never-show" not in result.text
        assert (
            await client.get("/settings/antigravity-status?node=worker")
        ).status_code == 400
        unsupported = await client.get("/settings/antigravity-usage?node=worker")
        assert unsupported.status_code == 200
        assert unsupported.json() == {
            "status": "unsupported",
            "source": "live",
            "captured_at": None,
            "groups": [],
        }
    app.dependency_overrides.clear()
    app.state.auth_config = AuthConfig(admin_token="test-admin")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, path in (
            ("GET", "antigravity-status"),
            ("GET", "antigravity-usage"),
            ("POST", "antigravity-refresh"),
            ("POST", "antigravity-models"),
        ):
            denied = await client.request(method, "/settings/" + path)
            assert denied.status_code == 401
            assert denied.headers["x-auth-required"] == "admin"
        allowed = await client.get(
            "/settings/antigravity-status", headers={"x-admin-token": "test-admin"}
        )
        assert allowed.status_code == 200


def test_login_reports_owner_and_keys_and_custom_backends_are_rejected(
    monkeypatch, capsys
):
    monkeypatch.setattr(auth, "read_sources", lambda: [])
    assert login_cli("google-antigravity") == 1
    assert "agy" in capsys.readouterr().out
    with pytest.raises(ValueError, match="managed by agy"):
        api_keys.set_key("google-antigravity", "must-not-save")
    with pytest.raises(ValueError, match="managed by agy"):
        api_keys.remove_key("google-antigravity")
    with pytest.raises(ValueError, match="fixed built-in"):
        llm_backends.save_backend_record(
            "copy", "google-antigravity", "https://example.org"
        )


@pytest.mark.asyncio
async def test_key_delete_is_a_client_error_for_borrowed_credentials():
    app = FastAPI()
    app.include_router(key_routes.router)
    app.dependency_overrides[get_service] = lambda: object()
    app.dependency_overrides[verify_admin_token] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.delete("/keys/google-antigravity")
        assert response.status_code == 400
        assert "managed by agy" in response.json()["detail"]
