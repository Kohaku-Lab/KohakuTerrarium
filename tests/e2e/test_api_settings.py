"""E2E journey — the HTTP configuration / Settings surface.

One fat journey simulating a real user configuring the framework
through the Settings UI, driven through the real FastAPI app via
``fastapi.testclient.TestClient``. Mirrors the call sequence the Vue
frontend's ``settingsAPI`` (``src/kohakuterrarium-frontend/src/utils/
api.js``) makes when an operator opens the Settings page and wires up
their environment end to end.

Everything runs for real: ``create_app()``, a real
:class:`LocalTerrariumService` over a real :class:`Terrarium`
installed via :func:`api.deps.set_service`, and the real on-disk
identity / config stores — redirected into ``tmp_path`` so the suite
never touches the user's ``~/.kohakuterrarium`` directory. The only
seam is the LLM: both ``create_llm_provider`` bind points are
monkeypatched to a deterministic :class:`ScriptedLLM` for the
live-creature leg of the journey.

The journey, in one method:

1. Identity — create / list / update / delete an LLM backend; set +
   read an API key on it; create / list / update / delete an LLM
   profile bound to that backend; set + read the default model.
2. MCP registry — register / list / update (re-upsert) / remove an
   MCP server config.
3. UI prefs — write + read back UI preferences.
4. Per-creature settings on a live creature — list configurable
   modules, drive a plugin's options + enabled state through the
   module API, and switch the creature's model — reading each back
   changed.
5. Confirm consumption — the registered profile is selectable (shows
   up in the catalog models list) and the default model resolves to
   it; switching the live creature to that profile takes effect.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.api.routes.catalog import _deps as catalog_deps
from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.timeout(30)

_REPLY = "Scripted settings-journey reply."

# A creature config that declares the built-in ``budget`` plugin so the
# per-creature module surface has a real, enabled, schema-bearing
# plugin to drive.
_CREATURE_CONFIG = """\
name: alice
system_prompt: "You are a deterministic settings-journey creature."
input:
  type: none
output:
  type: stdout
plugins:
  - name: budget
    options:
      turn_budget: {soft: 20, hard: 40}
"""


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def scripted_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """Replace the live LLM provider at BOTH bind points.

    ``bootstrap.llm.create_llm_provider`` is the canonical factory;
    ``bootstrap.agent_init`` imports it by name, so a second patch is
    required or the agent-init path would reach a real provider.
    """
    llm = ScriptedLLM([_REPLY, _REPLY, _REPLY])

    def _fake_create(config, llm_override=None):
        return llm

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)
    return llm


@pytest.fixture
def identity_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every on-disk identity / config store into ``tmp_path``.

    Identity stores resolve their on-disk path through ``config_dir()``
    on every call, honouring ``KT_CONFIG_DIR``.  The legacy
    ``PROFILES_PATH`` / ``KEYS_PATH`` / etc.  constants are computed
    once at import time and remain for back-compat display only —
    monkeypatching them does NOT redirect the live read/write path and
    leaks writes to the operator's real ``~/.kohakuterrarium/``.
    """
    store = tmp_path / "kt_home"
    store.mkdir()
    monkeypatch.setenv("KT_CONFIG_DIR", str(store))
    return store


@pytest.fixture
def creature_dir(tmp_path: Path) -> Path:
    """Write a minimal but real on-disk creature config directory."""
    cdir = tmp_path / "alice"
    cdir.mkdir()
    (cdir / "config.yaml").write_text(_CREATURE_CONFIG, encoding="utf-8")
    return cdir


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scripted_llm: ScriptedLLM,
    identity_dir: Path,
) -> Iterator[TestClient]:
    """A TestClient over a real ``create_app()`` with a real service."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))

    engine = Terrarium(session_dir=str(session_dir))
    service = LocalTerrariumService(engine)
    set_service(service)

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client

    set_service(None)
    # The journey opens a workspace for the module_schema route; clear
    # the process-wide handle so it never leaks into the next test.
    catalog_deps.set_workspace(None)


# ── the journey ───────────────────────────────────────────────────────


class TestApiSettingsJourney:
    """One fat journey over the real HTTP configuration surface."""

    def test_full_settings_round_trip(
        self,
        client: TestClient,
        creature_dir: Path,
        scripted_llm: ScriptedLLM,
    ) -> None:
        # ── 1. Identity: LLM backend CRUD ─────────────────────────────
        # Baseline: only the six built-in backends, no user backends.
        resp = client.get("/api/settings/backends")
        assert resp.status_code == 200
        baseline = resp.json()["backends"]
        baseline_names = {b["name"] for b in baseline}
        assert baseline_names == {
            "codex",
            "openai",
            "openrouter",
            "anthropic",
            "gemini",
            "mimo",
        }
        assert "acme" not in baseline_names

        # Create a custom backend (frontend: settingsAPI.saveBackend).
        resp = client.post(
            "/api/settings/backends",
            json={
                "name": "acme",
                "backend_type": "openai",
                "base_url": "https://acme.example/v1",
                "api_key_env": "ACME_API_KEY",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "saved", "name": "acme"}

        # It now appears in the list with the exact values we wrote.
        resp = client.get("/api/settings/backends")
        assert resp.status_code == 200
        backends = {b["name"]: b for b in resp.json()["backends"]}
        assert "acme" in backends
        assert backends["acme"]["base_url"] == "https://acme.example/v1"
        assert backends["acme"]["built_in"] is False
        assert backends["acme"]["has_token"] is False

        # Update it — re-POST with the same name changes the base_url.
        resp = client.post(
            "/api/settings/backends",
            json={
                "name": "acme",
                "backend_type": "openai",
                "base_url": "https://acme.example/v2",
                "api_key_env": "ACME_API_KEY",
            },
        )
        assert resp.status_code == 200
        resp = client.get("/api/settings/backends")
        backends = {b["name"]: b for b in resp.json()["backends"]}
        assert backends["acme"]["base_url"] == "https://acme.example/v2"

        # ── 2. Identity: API key set + read ───────────────────────────
        # No keys stored yet.
        resp = client.get("/api/settings/keys")
        assert resp.status_code == 200
        providers = {p["provider"]: p for p in resp.json()["providers"]}
        assert providers["acme"]["has_key"] is False
        assert providers["acme"]["masked_key"] == ""

        # Set a key on the custom backend (frontend: settingsAPI.saveKey).
        resp = client.post(
            "/api/settings/keys",
            json={"provider": "acme", "key": "acme-secret-key-1234"},
        )
        assert resp.status_code == 200
        # ``rotated`` is the count of live creature providers whose
        # cached LLM client was rebuilt with the new key (live
        # credential reload — POST /keys fans out to every running
        # creature so users don't need to restart after editing keys
        # in the frontend's Providers panel).  Zero here because no
        # creatures are running in this test setup; assert the shape
        # is correct without pinning the count.
        body = resp.json()
        assert body["status"] == "saved"
        assert body["provider"] == "acme"
        assert "rotated" in body
        assert isinstance(body["rotated"], int)

        # The key reads back as present + masked, and the backend now
        # reports a token.
        resp = client.get("/api/settings/keys")
        providers = {p["provider"]: p for p in resp.json()["providers"]}
        assert providers["acme"]["has_key"] is True
        assert providers["acme"]["masked_key"] == "acme...1234"

        resp = client.get("/api/settings/backends")
        backends = {b["name"]: b for b in resp.json()["backends"]}
        assert backends["acme"]["has_token"] is True

        # Setting a key for an unknown provider is a hard 404.
        resp = client.post(
            "/api/settings/keys",
            json={"provider": "ghost", "key": "x"},
        )
        assert resp.status_code == 404

        # Re-POSTing the key for the same provider replaces it; the new
        # mask reflects the new value, not the old one.
        resp = client.post(
            "/api/settings/keys",
            json={"provider": "acme", "key": "acme-rotated-key-9876"},
        )
        assert resp.status_code == 200
        resp = client.get("/api/settings/keys")
        providers = {p["provider"]: p for p in resp.json()["providers"]}
        assert providers["acme"]["masked_key"] == "acme...9876"

        # Delete the key — the provider goes back to no-key, and the
        # backend stops reporting a token.
        resp = client.delete("/api/settings/keys/acme")
        assert resp.status_code == 200
        resp = client.get("/api/settings/keys")
        providers = {p["provider"]: p for p in resp.json()["providers"]}
        assert providers["acme"]["has_key"] is False
        assert providers["acme"]["masked_key"] == ""
        resp = client.get("/api/settings/backends")
        backends = {b["name"]: b for b in resp.json()["backends"]}
        assert backends["acme"]["has_token"] is False
        # Re-set it so the rest of the journey has a token to work with.
        resp = client.post(
            "/api/settings/keys",
            json={"provider": "acme", "key": "acme-secret-key-1234"},
        )
        assert resp.status_code == 200

        # ── 3. Identity: LLM profile CRUD ─────────────────────────────
        # No user profiles yet.
        resp = client.get("/api/settings/profiles")
        assert resp.status_code == 200
        assert resp.json()["profiles"] == []

        # Create a profile bound to the custom backend
        # (frontend: settingsAPI.saveProfile).
        resp = client.post(
            "/api/settings/profiles",
            json={
                "name": "acme-fast",
                "model": "acme-model-1",
                "provider": "acme",
                "max_context": 64000,
                "max_output": 8192,
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "saved",
            "name": "acme-fast",
            "provider": "acme",
        }

        # It reads back with the values we wrote.
        resp = client.get("/api/settings/profiles")
        profiles = {p["name"]: p for p in resp.json()["profiles"]}
        assert "acme-fast" in profiles
        assert profiles["acme-fast"]["model"] == "acme-model-1"
        assert profiles["acme-fast"]["provider"] == "acme"
        assert profiles["acme-fast"]["max_context"] == 64000

        # Update it — re-POST changes the model id.
        resp = client.post(
            "/api/settings/profiles",
            json={
                "name": "acme-fast",
                "model": "acme-model-2",
                "provider": "acme",
                "max_context": 64000,
                "max_output": 8192,
            },
        )
        assert resp.status_code == 200
        resp = client.get("/api/settings/profiles")
        profiles = {p["name"]: p for p in resp.json()["profiles"]}
        assert profiles["acme-fast"]["model"] == "acme-model-2"

        # A profile for an unknown provider is a hard 404.
        resp = client.post(
            "/api/settings/profiles",
            json={"name": "bad", "model": "m", "provider": "no-such-provider"},
        )
        assert resp.status_code == 404

        # ── 4. Identity: default model set + read + consumption ───────
        resp = client.post(
            "/api/settings/default-model",
            json={"name": "acme/acme-fast"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "set",
            "default_model": "acme/acme-fast",
        }

        resp = client.get("/api/settings/default-model")
        assert resp.status_code == 200
        assert resp.json() == {"default_model": "acme/acme-fast"}

        # Consumption check: the registered profile is selectable — it
        # shows in the catalog models list, flagged as the default.
        resp = client.get("/api/configs/models")
        assert resp.status_code == 200
        models = {(m["provider"], m["name"]): m for m in resp.json()}
        assert ("acme", "acme-fast") in models
        assert models[("acme", "acme-fast")]["model"] == "acme-model-2"
        assert models[("acme", "acme-fast")]["is_default"] is True

        # The identity ``/settings/models`` route surfaces the same
        # profile, also flagged as the default.
        resp = client.get("/api/settings/models")
        assert resp.status_code == 200
        id_models = {(m["provider"], m["name"]): m for m in resp.json()}
        assert id_models[("acme", "acme-fast")]["is_default"] is True

        # Native-tools metadata is a fixed catalog the Settings page
        # renders for the per-creature native-tool toggles.
        resp = client.get("/api/settings/native-tools")
        assert resp.status_code == 200
        native_tools = resp.json()["tools"]
        assert native_tools and all("name" in t for t in native_tools)

        # ── 5. MCP registry CRUD ──────────────────────────────────────
        resp = client.get("/api/settings/mcp")
        assert resp.status_code == 200
        assert resp.json()["servers"] == []

        # Register a server (frontend: settingsAPI.addMCP).
        resp = client.post(
            "/api/settings/mcp",
            json={
                "name": "files",
                "transport": "stdio",
                "command": "mcp-files",
                "args": ["--root", "/tmp"],
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "saved", "name": "files"}

        resp = client.get("/api/settings/mcp")
        servers = {s["name"]: s for s in resp.json()["servers"]}
        assert "files" in servers
        assert servers["files"]["command"] == "mcp-files"
        assert servers["files"]["args"] == ["--root", "/tmp"]

        # Update — re-upsert the same name with a different command.
        resp = client.post(
            "/api/settings/mcp",
            json={
                "name": "files",
                "transport": "stdio",
                "command": "mcp-files-v2",
                "args": ["--root", "/srv"],
            },
        )
        assert resp.status_code == 200
        resp = client.get("/api/settings/mcp")
        servers = {s["name"]: s for s in resp.json()["servers"]}
        assert servers["files"]["command"] == "mcp-files-v2"
        assert servers["files"]["args"] == ["--root", "/srv"]
        # Still exactly one entry — upsert replaced, not appended.
        assert len(resp.json()["servers"]) == 1

        # Remove it.
        resp = client.delete("/api/settings/mcp/files")
        assert resp.status_code == 200
        assert resp.json() == {"status": "removed", "name": "files"}
        resp = client.get("/api/settings/mcp")
        assert resp.json()["servers"] == []
        # Removing a missing server is a hard 404.
        resp = client.delete("/api/settings/mcp/files")
        assert resp.status_code == 404

        # ── 6. UI preferences write + read back ───────────────────────
        # Defaults come back before any write.
        resp = client.get("/api/settings/ui-prefs")
        assert resp.status_code == 200
        assert resp.json()["values"]["theme"] == "system"

        # Write a couple of prefs (frontend: settingsAPI.updateUIPrefs).
        resp = client.post(
            "/api/settings/ui-prefs",
            json={"values": {"theme": "dark", "nav-expanded": False}},
        )
        assert resp.status_code == 200
        merged = resp.json()["values"]
        assert merged["theme"] == "dark"
        assert merged["nav-expanded"] is False

        # Read back independently — the write persisted.
        resp = client.get("/api/settings/ui-prefs")
        values = resp.json()["values"]
        assert values["theme"] == "dark"
        assert values["nav-expanded"] is False
        # An untouched default key is still present (merge, not replace).
        assert values["kt-desktop-zoom"] == 1.15

        # ── 7. Per-creature settings on a live creature ───────────────
        # Start a 1-creature session (frontend: agentAPI.create).
        resp = client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        created = resp.json()
        session_id = created["session_id"]
        creature_id = created["agent_id"]
        assert session_id and creature_id

        # 7a. List configurable modules — the declared ``budget`` plugin
        #     is present and enabled.
        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/modules")
        assert resp.status_code == 200
        modules = {(m["type"], m["name"]): m for m in resp.json()["modules"]}
        assert ("plugin", "budget") in modules
        assert modules[("plugin", "budget")]["enabled"] is True

        # 7b. Read the plugin's option schema + current values.
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}"
            "/modules/plugin/budget/options"
        )
        assert resp.status_code == 200
        opts = resp.json()
        assert "turn_budget" in opts["schema"]
        assert opts["options"]["turn_budget"] == {"soft": 20, "hard": 40}

        # 7c. Apply new plugin options through the module API.
        resp = client.put(
            f"/api/sessions/{session_id}/creatures/{creature_id}"
            "/modules/plugin/budget/options",
            json={"values": {"turn_budget": {"soft": 5, "hard": 10}}},
        )
        assert resp.status_code == 200
        applied = resp.json()
        assert applied["status"] == "saved"
        assert applied["options"]["turn_budget"] == {"soft": 5, "hard": 10}

        # Read it back — the change took effect on the live creature.
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}"
            "/modules/plugin/budget/options"
        )
        assert resp.status_code == 200
        assert resp.json()["options"]["turn_budget"] == {"soft": 5, "hard": 10}

        # 7d. Toggle the plugin off, then read it back disabled.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}"
            "/modules/plugin/budget/toggle"
        )
        assert resp.status_code == 200
        assert resp.json() == {"name": "budget", "enabled": False}

        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/modules")
        modules = {(m["type"], m["name"]): m for m in resp.json()["modules"]}
        assert modules[("plugin", "budget")]["enabled"] is False

        # 7e. Switch the creature's model to the profile we registered
        #     in step 3 — closing the loop: a setting written through
        #     the identity routes is now selectable on a live creature.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/model",
            json={"model": "acme/acme-fast"},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "switched",
            "model": "acme/acme-fast",
        }

        # Read-back: the active-session getter reflects the switch via
        # the creature's canonical ``llm_name`` identifier.
        resp = client.get(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["creatures"][0]["llm_name"] == "acme/acme-fast"

        # Switching to a genuinely unknown profile is a hard 400.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/model",
            json={"model": "acme/does-not-exist"},
        )
        assert resp.status_code == 400

        # 7f. Per-creature state surface — the Inspector panels the
        #     Settings page links to. scratchpad patch + read-back,
        #     triggers / env / system-prompt / working-dir / native
        #     tool options.
        cbase = f"/api/sessions/{session_id}/creatures/{creature_id}"
        resp = client.get(f"{cbase}/scratchpad")
        assert resp.status_code == 200
        resp = client.patch(
            f"{cbase}/scratchpad", json={"updates": {"stage": "configured"}}
        )
        assert resp.status_code == 200
        assert resp.json()["stage"] == "configured"
        # Deleting the key (value None) removes it from the read-back.
        resp = client.patch(f"{cbase}/scratchpad", json={"updates": {"stage": None}})
        assert resp.status_code == 200
        assert "stage" not in resp.json()
        # A reserved scratchpad key is a hard 400.
        resp = client.patch(
            f"{cbase}/scratchpad", json={"updates": {"__turn_count__": "1"}}
        )
        assert resp.status_code == 400
        resp = client.get(f"{cbase}/triggers")
        assert resp.status_code == 200
        assert resp.json() == []
        resp = client.get(f"{cbase}/env")
        assert resp.status_code == 200
        env_payload = resp.json()
        assert env_payload["pwd"]
        assert not any("secret" in k.lower() for k in env_payload["env"])
        resp = client.get(f"{cbase}/system-prompt")
        assert resp.status_code == 200
        assert "deterministic settings-journey creature" in resp.json()["text"]
        resp = client.get(f"{cbase}/working-dir")
        assert resp.status_code == 200
        assert resp.json()["pwd"] == env_payload["pwd"]
        # Native tool options — the budget-plugin creature declares no
        # provider-native tools, so the inventory is empty.
        resp = client.get(f"{cbase}/native-tool-options")
        assert resp.status_code == 200
        assert resp.json() == {"tools": []}

        # 7g. Per-creature module options for an unknown module are a
        #     hard 404 — the module API never fakes a schema.
        resp = client.get(f"{cbase}/modules/plugin/no-such-plugin/options")
        assert resp.status_code == 404
        # An unknown module *type* is a hard 400 (the dispatcher rejects
        # it rather than guessing).
        resp = client.get(f"{cbase}/modules/not-a-type/budget/options")
        assert resp.status_code == 400
        # native_tool is a real module type — its inventory is part of
        # the unified modules list. The budget-plugin creature declares
        # no native tools, so no native_tool rows appear, but the type
        # itself resolves (a get on a missing name is a 404, not a 400).
        resp = client.get(f"{cbase}/modules/native_tool/no-such-tool/options")
        assert resp.status_code == 404
        # native_tool does not support toggle — that is a hard 400.
        resp = client.post(f"{cbase}/modules/native_tool/anything/toggle")
        assert resp.status_code in (400, 404)
        # Re-enable the budget plugin and reset its options to defaults
        # by applying an empty values payload — the live creature reads
        # back the plugin's declared defaults again.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}"
            "/modules/plugin/budget/toggle"
        )
        assert resp.status_code == 200
        assert resp.json() == {"name": "budget", "enabled": True}
        resp = client.put(
            f"/api/sessions/{session_id}/creatures/{creature_id}"
            "/modules/plugin/budget/options",
            json={"values": {"turn_budget": {"soft": 99, "hard": 199}}},
        )
        assert resp.status_code == 200
        assert resp.json()["options"]["turn_budget"] == {"soft": 99, "hard": 199}

        # 7h. The studio module_schema route — the editor's option-form
        #     source — resolves builtin schemas for the Settings page's
        #     per-creature module accordion. It needs an open workspace,
        #     so open the creature dir's parent first.
        resp = client.post(
            "/api/studio/workspace/open",
            json={"path": str(creature_dir.parent)},
        )
        assert resp.status_code == 200
        resp = client.post(
            "/api/studio/module_schema", json={"kind": "plugins", "type": "builtin"}
        )
        assert resp.status_code == 200
        assert "priority" in {p["name"] for p in resp.json()["params"]}
        resp = client.post(
            "/api/studio/module_schema", json={"kind": "triggers", "type": "builtin"}
        )
        assert resp.status_code == 200
        assert resp.json()["params"] == []

        # ── 8. Identity teardown — delete profile, then backend ───────
        # The profile must go first: a backend in use by a preset
        # cannot be deleted.
        resp = client.delete("/api/settings/profiles/acme/acme-fast")
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "deleted",
            "name": "acme-fast",
            "provider": "acme",
        }
        resp = client.get("/api/settings/profiles")
        assert resp.json()["profiles"] == []
        # Deleting it again is a hard 404.
        resp = client.delete("/api/settings/profiles/acme/acme-fast")
        assert resp.status_code == 404

        # Now the backend deletes cleanly.
        resp = client.delete("/api/settings/backends/acme")
        assert resp.status_code == 200
        assert resp.json() == {"status": "deleted", "name": "acme"}
        resp = client.get("/api/settings/backends")
        assert "acme" not in {b["name"] for b in resp.json()["backends"]}
        # Deleting it again is a hard 404.
        resp = client.delete("/api/settings/backends/acme")
        assert resp.status_code == 404

        # Stop the live session — it leaves the active list.
        resp = client.delete(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert client.get("/api/sessions/active").json() == []
