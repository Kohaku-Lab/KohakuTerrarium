"""Unit tests for :mod:`kohakuterrarium.api.routes.identity.{mcp,llm}`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.identity import llm as llm_mod
from kohakuterrarium.api.routes.identity import mcp as mcp_mod
from kohakuterrarium.llm import profiles


def _app(*routers) -> FastAPI:
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    return app


# ── MCP ──────────────────────────────────────────────────────────


class TestMcpRoutes:
    def test_list(self, monkeypatch):
        monkeypatch.setattr(mcp_mod, "load_servers", lambda: [{"name": "s1"}])
        client = TestClient(_app(mcp_mod.router))
        resp = client.get("/mcp")
        assert resp.status_code == 200
        assert resp.json()["servers"] == [{"name": "s1"}]

    def test_upsert(self, monkeypatch):
        captured = []
        monkeypatch.setattr(mcp_mod, "upsert_server", lambda d: captured.append(d) or d)
        client = TestClient(_app(mcp_mod.router))
        resp = client.post(
            "/mcp",
            json={"name": "s1", "transport": "stdio", "command": "echo"},
        )
        assert resp.status_code == 200
        assert captured[0]["name"] == "s1"

    def test_upsert_validation_error(self, monkeypatch):
        def boom(d):
            raise ValueError("Name is required")

        monkeypatch.setattr(mcp_mod, "upsert_server", boom)
        client = TestClient(_app(mcp_mod.router))
        resp = client.post("/mcp", json={"name": "x"})
        assert resp.status_code == 400

    def test_delete(self, monkeypatch):
        monkeypatch.setattr(mcp_mod, "delete_server", lambda n: True)
        client = TestClient(_app(mcp_mod.router))
        resp = client.delete("/mcp/x")
        assert resp.status_code == 200

    def test_delete_missing(self, monkeypatch):
        monkeypatch.setattr(mcp_mod, "delete_server", lambda n: False)
        client = TestClient(_app(mcp_mod.router))
        resp = client.delete("/mcp/missing")
        assert resp.status_code == 404


# ── LLM backends + profiles ─────────────────────────────────────


class TestLlmBackendsRoutes:
    def test_get_backends(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "list_backends", lambda: [{"name": "openai"}])
        client = TestClient(_app(llm_mod.router))
        resp = client.get("/backends")
        assert resp.status_code == 200
        assert resp.json()["backends"][0]["name"] == "openai"

    def test_create_backend(self, monkeypatch):
        captured = {}

        def fake_save(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(llm_mod, "save_backend_record", fake_save)
        client = TestClient(_app(llm_mod.router))
        resp = client.post("/backends", json={"name": "my", "backend_type": "openai"})
        assert resp.status_code == 200
        assert captured["name"] == "my"

    def test_create_backend_validation_error(self, monkeypatch):
        def boom(**k):
            raise ValueError("Unsupported backend type")

        monkeypatch.setattr(llm_mod, "save_backend_record", boom)
        client = TestClient(_app(llm_mod.router))
        resp = client.post(
            "/backends",
            json={"name": "my", "backend_type": "weird"},
        )
        assert resp.status_code == 400

    def test_delete_backend_success(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "remove_backend", lambda n: True)
        client = TestClient(_app(llm_mod.router))
        resp = client.delete("/backends/my")
        assert resp.status_code == 200

    def test_delete_backend_missing(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "remove_backend", lambda n: False)
        client = TestClient(_app(llm_mod.router))
        resp = client.delete("/backends/ghost")
        assert resp.status_code == 404

    def test_delete_backend_value_error(self, monkeypatch):
        def boom(n):
            raise ValueError("cannot delete built-in")

        monkeypatch.setattr(llm_mod, "remove_backend", boom)
        client = TestClient(_app(llm_mod.router))
        resp = client.delete("/backends/openai")
        assert resp.status_code == 400


class TestLlmProfilesRoutes:
    def test_get_profiles(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "list_profiles_payload", lambda: [{"name": "p"}])
        client = TestClient(_app(llm_mod.router))
        resp = client.get("/profiles")
        assert resp.status_code == 200

    def test_native_tools(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "list_native_tools", lambda: [{"x": 1}])
        client = TestClient(_app(llm_mod.router))
        resp = client.get("/native-tools")
        assert resp.status_code == 200
        assert resp.json() == {"tools": [{"x": 1}]}

    def test_create_profile(self, monkeypatch):
        # The profile-record write touches the user's identity config
        # (true I/O) — stub it and assert the route's echo contract.
        captured = {}
        monkeypatch.setattr(
            llm_mod,
            "save_profile_record",
            lambda **kw: captured.update(kw),
        )
        client = TestClient(_app(llm_mod.router))
        resp = client.post(
            "/profiles",
            json={"name": "fast", "model": "gpt-4", "provider": "openai"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "saved",
            "name": "fast",
            "provider": "openai",
        }
        # The route forwarded the request fields to the record writer.
        assert captured["name"] == "fast"
        assert captured["model"] == "gpt-4"

    def test_create_profile_unknown_provider_404(self, monkeypatch):
        # A "Provider not found" ValueError maps to 404 (not the generic
        # 400) — the route inspects the message prefix.
        def _boom(**k):
            raise ValueError("Provider not found: ghost")

        monkeypatch.setattr(llm_mod, "save_profile_record", _boom)
        client = TestClient(_app(llm_mod.router))
        resp = client.post(
            "/profiles", json={"name": "p", "model": "m", "provider": "ghost"}
        )
        assert resp.status_code == 404

    def test_create_profile_other_value_error_400(self, monkeypatch):
        def _boom(**k):
            raise ValueError("temperature out of range")

        monkeypatch.setattr(llm_mod, "save_profile_record", _boom)
        client = TestClient(_app(llm_mod.router))
        resp = client.post("/profiles", json={"name": "p", "model": "m"})
        assert resp.status_code == 400

    def test_delete_profile_success(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "remove_profile", lambda name, provider: True)
        client = TestClient(_app(llm_mod.router))
        resp = client.delete("/profiles/openai/fast")
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "deleted",
            "name": "fast",
            "provider": "openai",
        }

    def test_delete_profile_missing_404(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "remove_profile", lambda name, provider: False)
        client = TestClient(_app(llm_mod.router))
        resp = client.delete("/profiles/openai/ghost")
        assert resp.status_code == 404


class TestLlmDefaultModelRoutes:
    """The default-model routes against the real profile store.

    ``KT_CONFIG_DIR`` is a per-test tmp dir (``tests/conftest.py``
    ``_default_isolated_config_dir``) and every profile read/write
    resolves it fresh, so these drive the real resolver instead of a
    stub — the resolution is the behaviour under test.
    """

    def _client(self) -> TestClient:
        return TestClient(_app(llm_mod.router))

    def _register_acme(self, client: TestClient) -> None:
        """Create the ``acme`` backend plus its ``acme-fast`` profile."""
        resp = client.post("/backends", json={"name": "acme", "backend_type": "openai"})
        assert resp.status_code == 200
        resp = client.post(
            "/profiles",
            json={"name": "acme-fast", "model": "acme-model-1", "provider": "acme"},
        )
        assert resp.status_code == 200

    def test_get_default_model(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "get_default", lambda: "openai/gpt-4")
        client = self._client()
        resp = client.get("/default-model")
        assert resp.status_code == 200
        assert resp.json() == {"default_model": "openai/gpt-4"}

    def test_set_default_model_upgrades_bare_user_profile(self):
        client = self._client()
        self._register_acme(client)
        resp = client.post("/default-model", json={"name": "acme-fast"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "set", "default_model": "acme/acme-fast"}
        assert client.get("/default-model").json() == {
            "default_model": "acme/acme-fast"
        }

    def test_set_default_model_accepts_qualified_user_profile(self):
        client = self._client()
        self._register_acme(client)
        resp = client.post("/default-model", json={"name": "acme/acme-fast"})
        assert resp.status_code == 200
        assert resp.json() == {"status": "set", "default_model": "acme/acme-fast"}
        assert client.get("/default-model").json() == {
            "default_model": "acme/acme-fast"
        }

    def test_set_default_model_qualified_builtin(self):
        client = self._client()
        resp = client.post("/default-model", json={"name": "anthropic/claude-opus-4.8"})
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "set",
            "default_model": "anthropic/claude-opus-4.8",
        }
        assert client.get("/default-model").json() == {
            "default_model": "anthropic/claude-opus-4.8"
        }

    def test_set_default_model_ambiguous_bare_name_rejected(self):
        # ``claude-opus-4.8`` ships under both anthropic and openrouter.
        # The bare name used to persist verbatim and read back as the
        # openrouter row no matter which provider the user clicked.
        client = self._client()
        self._register_acme(client)
        assert (
            client.post("/default-model", json={"name": "acme-fast"}).status_code == 200
        )
        resp = client.post("/default-model", json={"name": "claude-opus-4.8"})
        assert resp.status_code == 400
        assert "exists under multiple providers" in resp.json()["detail"]
        # The rejected write left the previous default untouched.
        assert client.get("/default-model").json() == {
            "default_model": "acme/acme-fast"
        }
        assert profiles._load_yaml().get("default_model", "") == "acme/acme-fast"

    def test_set_default_model_unknown_name_404(self):
        client = self._client()
        self._register_acme(client)
        assert (
            client.post("/default-model", json={"name": "acme-fast"}).status_code == 200
        )
        resp = client.post("/default-model", json={"name": "no-such-preset"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Preset not found: no-such-preset"
        assert client.get("/default-model").json() == {
            "default_model": "acme/acme-fast"
        }

    def test_set_default_model_empty_name_clears(self):
        client = self._client()
        self._register_acme(client)
        assert (
            client.post("/default-model", json={"name": "acme-fast"}).status_code == 200
        )
        resp = client.post("/default-model", json={"name": ""})
        assert resp.status_code == 200
        assert resp.json() == {"status": "set", "default_model": ""}
        # The explicit default is gone from the store; what ``GET`` now
        # reports is the built-in fallback, not a persisted choice.
        assert profiles._load_yaml().get("default_model", "") == ""

    def test_get_all_models(self, monkeypatch):
        monkeypatch.setattr(
            llm_mod, "list_all_models_combined", lambda: {"models": ["a", "b"]}
        )
        client = TestClient(_app(llm_mod.router))
        resp = client.get("/models")
        assert resp.status_code == 200
        assert resp.json() == {"models": ["a", "b"]}
