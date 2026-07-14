"""Unit tests for the API identity routes (ui_prefs, codex, api_keys)."""

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.identity import api_keys as api_keys_mod
from kohakuterrarium.api.routes.identity import codex as codex_mod
from kohakuterrarium.api.routes.identity import ui_prefs as ui_prefs_mod


def _app(*routers) -> FastAPI:
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return app


# ── ui_prefs route ──────────────────────────────────────────────


class TestUiPrefsRoute:
    # Signatures take ``user_id=None`` because the route now passes
    # the (anonymous = None) user id through to the studio layer for
    # per-user prefs scoping under L4.

    def test_get(self, monkeypatch):
        monkeypatch.setattr(
            ui_prefs_mod, "load_prefs", lambda user_id=None: {"theme": "dark"}
        )
        client = TestClient(_app(ui_prefs_mod.router))
        resp = client.get("/ui-prefs")
        assert resp.status_code == 200
        assert resp.json() == {"values": {"theme": "dark"}}

    def test_post_persists(self, monkeypatch):
        captured = {}

        def fake_save(values, *, user_id=None):
            captured["values"] = values
            captured["user_id"] = user_id
            return {"theme": "light", **values}

        monkeypatch.setattr(ui_prefs_mod, "save_prefs", fake_save)
        client = TestClient(_app(ui_prefs_mod.router))
        resp = client.post("/ui-prefs", json={"values": {"theme": "light"}})
        assert resp.status_code == 200
        assert resp.json()["values"]["theme"] == "light"
        assert captured["values"] == {"theme": "light"}
        # Anonymous (no L4 in this mini-app) → None → shared slot.
        assert captured["user_id"] is None

    def test_post_empty(self, monkeypatch):
        monkeypatch.setattr(ui_prefs_mod, "save_prefs", lambda v, *, user_id=None: {})
        client = TestClient(_app(ui_prefs_mod.router))
        resp = client.post("/ui-prefs", json={})
        assert resp.status_code == 200


# ── codex route ─────────────────────────────────────────────────


class TestCodexRoute:
    def test_status(self, monkeypatch):
        monkeypatch.setattr(codex_mod, "get_status", lambda: {"authenticated": False})
        client = TestClient(_app(codex_mod.router))
        resp = client.get("/codex-status")
        assert resp.status_code == 200
        assert resp.json() == {"authenticated": False}

    def test_login_success(self, monkeypatch):
        async def fake_login():
            return {"status": "ok", "expires_at": 1234}

        monkeypatch.setattr(codex_mod, "login_async", fake_login)
        client = TestClient(_app(codex_mod.router))
        resp = client.post("/codex-login")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "expires_at": 1234}

    def test_login_failure(self, monkeypatch):
        async def fake_login():
            raise RuntimeError("link dead")

        monkeypatch.setattr(codex_mod, "login_async", fake_login)
        client = TestClient(_app(codex_mod.router))
        resp = client.post("/codex-login")
        assert resp.status_code == 500
        assert "Codex login failed" in resp.json()["detail"]

    def test_usage_success(self, monkeypatch):
        async def fake_usage():
            return {"status": "ok", "snapshots": []}

        monkeypatch.setattr(codex_mod, "get_usage_async", fake_usage)
        client = TestClient(_app(codex_mod.router))
        resp = client.get("/codex-usage")
        assert resp.status_code == 200

    def test_usage_failure(self, monkeypatch):
        async def fake_usage():
            raise RuntimeError("refresh failed")

        monkeypatch.setattr(codex_mod, "get_usage_async", fake_usage)
        client = TestClient(_app(codex_mod.router))
        resp = client.get("/codex-usage")
        assert resp.status_code == 401

    def test_reset_consume_success(self, monkeypatch):
        captured = {}

        async def fake_consume(*, idempotency_key=None, credit_id=None):
            captured["idempotency_key"] = idempotency_key
            captured["credit_id"] = credit_id
            return {"outcome": "reset", "idempotency_key": "k-1"}

        monkeypatch.setattr(codex_mod, "consume_reset_credit_async", fake_consume)
        client = TestClient(_app(codex_mod.router))
        resp = client.post("/codex-reset-consume", json={"credit_id": "credit-9"})
        assert resp.status_code == 200
        assert resp.json() == {"outcome": "reset", "idempotency_key": "k-1"}
        # Empty/absent idempotency_key becomes None (server generates one).
        assert captured == {"idempotency_key": None, "credit_id": "credit-9"}

    def test_reset_consume_not_authenticated_is_401(self, monkeypatch):
        async def fake_consume(*, idempotency_key=None, credit_id=None):
            raise PermissionError("Codex account is not authenticated")

        monkeypatch.setattr(codex_mod, "consume_reset_credit_async", fake_consume)
        client = TestClient(_app(codex_mod.router))
        resp = client.post("/codex-reset-consume", json={})
        assert resp.status_code == 401

    def test_reset_consume_backend_error_is_502(self, monkeypatch):
        async def fake_consume(*, idempotency_key=None, credit_id=None):
            raise RuntimeError("backend 500")

        monkeypatch.setattr(codex_mod, "consume_reset_credit_async", fake_consume)
        client = TestClient(_app(codex_mod.router))
        resp = client.post("/codex-reset-consume", json={})
        assert resp.status_code == 502

    def test_reset_consume_upstream_401_maps_to_401(self, monkeypatch):
        # A genuine ChatGPT auth rejection (tokens present but stale) must
        # surface as 401, not a misleading 502.
        async def fake_consume(*, idempotency_key=None, credit_id=None):
            request = httpx.Request("POST", "https://chatgpt.com/x")
            raise httpx.HTTPStatusError(
                "unauthorized",
                request=request,
                response=httpx.Response(401, request=request),
            )

        monkeypatch.setattr(codex_mod, "consume_reset_credit_async", fake_consume)
        client = TestClient(_app(codex_mod.router))
        resp = client.post("/codex-reset-consume", json={})
        assert resp.status_code == 401

    def test_reset_consume_upstream_500_stays_502(self, monkeypatch):
        async def fake_consume(*, idempotency_key=None, credit_id=None):
            request = httpx.Request("POST", "https://chatgpt.com/x")
            raise httpx.HTTPStatusError(
                "boom",
                request=request,
                response=httpx.Response(500, request=request),
            )

        monkeypatch.setattr(codex_mod, "consume_reset_credit_async", fake_consume)
        client = TestClient(_app(codex_mod.router))
        resp = client.post("/codex-reset-consume", json={})
        assert resp.status_code == 502


# ── api_keys route ──────────────────────────────────────────────


class TestApiKeysRoute:
    def test_list(self, monkeypatch):
        monkeypatch.setattr(
            api_keys_mod,
            "list_keys_payload",
            lambda: [{"provider": "openai", "has_key": True}],
        )
        client = TestClient(_app(api_keys_mod.router))
        resp = client.get("/keys")
        assert resp.status_code == 200
        assert resp.json()["providers"][0]["provider"] == "openai"

    def test_set_key_success(self, monkeypatch):
        captured = []

        def fake_set(provider, key):
            captured.append((provider, key))

        monkeypatch.setattr(api_keys_mod, "set_key", fake_set)
        client = TestClient(_app(api_keys_mod.router))
        resp = client.post("/keys", json={"provider": "openai", "key": "sk-abc"})
        assert resp.status_code == 200
        assert captured == [("openai", "sk-abc")]

    def test_set_key_validation_error(self, monkeypatch):
        def boom(p, k):
            raise ValueError("Provider and key are required")

        monkeypatch.setattr(api_keys_mod, "set_key", boom)
        client = TestClient(_app(api_keys_mod.router))
        resp = client.post("/keys", json={"provider": "", "key": ""})
        assert resp.status_code == 400

    def test_set_key_unknown_provider(self, monkeypatch):
        def boom(p, k):
            raise LookupError("Provider not found")

        monkeypatch.setattr(api_keys_mod, "set_key", boom)
        client = TestClient(_app(api_keys_mod.router))
        resp = client.post("/keys", json={"provider": "ghost", "key": "x"})
        assert resp.status_code == 404

    def test_remove_key(self, monkeypatch):
        captured = []

        def fake_remove(p):
            captured.append(p)

        monkeypatch.setattr(api_keys_mod, "remove_key", fake_remove)
        client = TestClient(_app(api_keys_mod.router))
        resp = client.delete("/keys/openai")
        assert resp.status_code == 200
        assert captured == ["openai"]

    def test_remove_key_unknown(self, monkeypatch):
        def boom(p):
            raise LookupError("Provider not found")

        monkeypatch.setattr(api_keys_mod, "remove_key", boom)
        client = TestClient(_app(api_keys_mod.router))
        resp = client.delete("/keys/ghost")
        assert resp.status_code == 404
