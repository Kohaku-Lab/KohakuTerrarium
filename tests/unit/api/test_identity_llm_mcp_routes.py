"""Unit tests for :mod:`kohakuterrarium.api.routes.identity.{mcp,llm}`."""

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.identity import llm as llm_mod
from kohakuterrarium.api.routes.identity import mcp as mcp_mod
from kohakuterrarium.llm import catalog_cache, discovery
from kohakuterrarium.llm.discovery import DiscoveredModel
from kohakuterrarium.llm.profiles import resolve as profiles

from tests.unit._fakes.package_backend import (
    echo_backend,
    install_backend_package,
    kt_home,  # noqa: F401
)


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

    def test_backend_types_include_package_types(self, kt_home):
        install_backend_package(kt_home, "pkg-x", [echo_backend()])
        client = TestClient(_app(llm_mod.router))
        rows = client.get("/backend-types").json()["backend_types"]
        package_rows = [r for r in rows if r["source"] == "package"]
        assert [(r["name"], r["package"]) for r in package_rows] == [("echo", "pkg-x")]
        assert "openai" in {r["name"] for r in rows}

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
    def test_new_draft_cannot_replace_a_preset_saved_in_another_window(self):
        client = TestClient(_app(llm_mod.router))
        original = {
            "provider": "deepseek",
            "model": "tuned",
            "name": "flash",
            "max_context": 8000,
        }
        assert client.post("/profiles", json=original).is_success
        response = client.post(
            "/profiles", json={**original, "model": "new", "create_only": True}
        )
        assert response.status_code == 409
        assert profiles.get_profile("flash", "deepseek").model == "tuned"

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

    def test_legacy_context_group_is_not_shown_and_survives_an_editor_save(self):
        groups = {
            "context": {
                "200k": {"max_context": 200000},
                "long": {"max_context": 1000000},
            },
            "reasoning": {"high": {"reasoning_effort": "high"}},
        }
        legacy = {"model": "m", "max_context": 200000, "variation_groups": groups}
        profiles._save_yaml({"version": 3, "presets": {"openai": {"cc": legacy}}})
        client = self._client()

        def shown(path, key):
            rows = client.get(path).json()
            rows = rows["profiles"] if path == "/profiles" else rows
            return next(r for r in rows if (r["provider"], r["name"]) == key)

        for path in ("/profiles", "/models"):
            row = shown(path, ("openai", "cc"))
            assert row["variation_groups"] == {"reasoning": groups["reasoning"]}
            assert row["context_window"] == 1000000

        # The editor posts back what it was shown, with one field changed.
        row = shown("/profiles", ("openai", "cc"))
        body = {k: row[k] for k in ("name", "model", "provider", "max_context")}
        body |= {"variation_groups": row["variation_groups"], "max_output": 4096}
        assert client.post("/profiles", json=body).is_success
        stored = profiles._load_yaml()["presets"]["openai"]["cc"]
        assert stored["variation_groups"] == groups
        assert stored["max_output"] == 4096
        assert profiles.get_profile("cc@context=long", "openai").max_context == 10**6

        # A discovery draft carries its aliases through the same route.
        draft = {"name": "d", "model": "m", "provider": "openai"}
        draft["context_aliases"] = {"long": 700000}
        assert client.post("/profiles", json=draft).is_success
        assert profiles.get_profile("d@long", "openai").max_context == 700000

    def test_get_all_models(self, monkeypatch):
        monkeypatch.setattr(
            llm_mod, "list_all_models_combined", lambda: {"models": ["a", "b"]}
        )
        client = TestClient(_app(llm_mod.router))
        resp = client.get("/models")
        assert resp.status_code == 200
        assert resp.json() == {"models": ["a", "b"]}


class TestDiscoveryRows:
    def test_rows_carry_drafts_existing_presets_and_windows(self, monkeypatch):
        levels = ("low", "medium", "high", "xhigh", "max", "ultra")

        reads = []

        async def catalog(name, *, transport=None):
            assert name == "codex"
            reads.append(name)
            return [
                DiscoveredModel(
                    "gpt-6.1-sol", max_context=500000, reasoning_levels=levels
                ),
                DiscoveredModel(
                    "gpt-6.7-sol", max_context=500000, reasoning_levels=levels
                ),
            ]

        monkeypatch.setattr(discovery, "discover_models", catalog)
        monkeypatch.setattr(catalog_cache, "_cache", catalog_cache.DiscoveryCache())
        client = TestClient(_app(llm_mod.router))
        first = client.post("/backends/codex/models").json()
        datetime.fromisoformat(first["fetched_at"])
        known, new = first["models"]
        cached = client.post("/backends/codex/models").json()
        assert cached["fetched_at"] == first["fetched_at"] and reads == ["codex"]
        refreshed = client.post("/backends/codex/models?refresh=true").json()
        assert reads == ["codex", "codex"]
        assert refreshed["fetched_at"] >= first["fetched_at"]
        assert refreshed["models"] == first["models"]
        assert known["has_preset"] is True
        assert known["reasoning_levels"] == list(levels)
        assert known["context_window"] == 500000
        assert known["preset"]["name"] == "gpt-6.1-sol"
        assert new["has_preset"] is False
        assert new["context_window"] == 500000
        draft = new["preset"]
        assert list(draft["variation_groups"]) == ["reasoning", "speed"]
        assert list(draft["variation_groups"]["reasoning"]) == list(levels)
        assert draft["max_context"] == 500000
        assert draft["context_window"] == 500000

        # The draft posts back unchanged through the editor route.
        assert client.post("/profiles", json=draft).is_success
        saved = profiles.get_profile("gpt-6.7-sol@context=700k", "codex")
        assert (saved.max_context, saved.context_window) == (700000, 500000)
        assert saved.reasoning_effort == draft["reasoning_effort"]
        listed = {
            (p["provider"], p["name"]): p
            for p in client.get("/profiles").json()["profiles"]
        }
        assert listed[("codex", "gpt-6.7-sol")]["context_window"] == 500000
        # Rows are rebuilt per request, so a cached catalog sees the new preset.
        rows = client.post("/backends/codex/models").json()["models"]
        assert reads == ["codex", "codex"]
        assert [row["has_preset"] for row in rows] == [True, True]

    def test_bad_variation_groups_are_rejected(self):
        client = TestClient(_app(llm_mod.router))
        resp = client.post(
            "/profiles",
            json={
                "name": "bad",
                "model": "m",
                "provider": "openai",
                "variation_groups": {"g": {"o": {"model": "x"}}},
            },
        )
        assert resp.status_code == 400
        assert "Unsupported variation patch target" in resp.json()["detail"]
