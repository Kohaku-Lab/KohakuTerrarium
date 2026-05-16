"""Integration test for the ``kohakuterrarium.api`` package.

This is the canonical *usage example* of ``api/``: a real
:func:`create_app` FastAPI app, a real
:class:`LocalTerrariumService` over a real :class:`Terrarium`
installed via :func:`api.deps.set_service`, driven through
``fastapi.testclient.TestClient`` over both HTTP and WebSocket.

The only seam is the LLM — both ``create_llm_provider`` bind points
(``bootstrap.llm`` and ``bootstrap.agent_init``) are monkeypatched to
a deterministic :class:`ScriptedLLM`. Everything else (engine,
session stores, persistence on disk, topology mutation, the WS attach
loop) runs for real.

Each ``TestApiIntegration`` method drives one COMPLETE workflow end to
end — mirroring the REST+WS call sequences the Vue frontend
(``src/kohakuterrarium-frontend/src/utils/api.js``) actually makes:

* :meth:`test_creature_session_lifecycle_workflow` — create a creature
  session, see it in the active list, stream a chat turn over the WS,
  flip per-creature settings (model / plugin / scratchpad / modules /
  native-tool-options), exercise the catalog / identity / metrics /
  attach surfaces a real client opens alongside the session, run the
  per-creature control + chat-branch routes, then delete it.
* :meth:`test_session_persistence_and_resume_workflow` — run a turn so
  the ``.kohakutr`` file fills, list saved sessions, walk the full
  persistence-viewer surface (tree / summary / turns / events / diff /
  export / history / memory search / fork), stop the live session,
  resume from disk, delete it.
* :meth:`test_terrarium_hotplug_and_runtime_graph_workflow` — hot-plug
  a second creature into a session, declare + wire + send to a shared
  channel, drive the output-wiring CRUD, snapshot the runtime graph
  over HTTP and over four WS streams (graph / observer / logs / files),
  prove the lab-only ``/api/nodes`` surface 404s in standalone mode.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kohakuterrarium.api.app import create_app
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.api.routes.catalog import _deps as _catalog_deps
from kohakuterrarium.bootstrap import agent_init as _agent_init
from kohakuterrarium.bootstrap import llm as _bootstrap_llm
from kohakuterrarium.core import agent_model as _agent_model
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.timeout(30)

# Deterministic assistant replies. The ScriptedLLM matches the *next*
# unused entry per call; a plain creature turn = one LLM call.
_REPLY_ONE = "Hello from the scripted creature."
_REPLY_TWO = "Second scripted creature reply."

_CREATURE_CONFIG = """\
name: alice
system_prompt: "You are a deterministic integration-test creature."
input:
  type: none
output:
  type: stdout
"""


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def scripted_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """Replace the live LLM provider at every bind point.

    ``bootstrap.llm.create_llm_provider`` is the canonical creature
    factory; ``bootstrap.agent_init`` imports it by name, so a second
    patch is required or the agent init path would reach a real
    provider. ``core.agent_model.switch_model`` reaches the live
    backend through a THIRD function — ``create_llm_from_profile_name``
    — so it is patched too: without it a model switch swaps the
    scripted provider out for a real one and every subsequent chat
    bypasses the seam (the B-fat2-api-1 limitation, now closed).

    One shared :class:`ScriptedLLM` backs every creature so the test
    can still assert on ``call_count``. The reply list is generous:
    each fat workflow drives many turns (HTTP chat / WS chat /
    regenerate / edit / rewind). ScriptedLLM matches the next unused
    entry per call; ``_REPLY_ONE`` is first so the opening turn of
    every workflow is deterministic.
    """
    llm = ScriptedLLM([_REPLY_ONE] + [_REPLY_TWO] * 60)

    def _fake_create(config, llm_override=None):
        return llm

    def _fake_from_profile_name(name):
        return llm

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)
    monkeypatch.setattr(
        _agent_model, "create_llm_from_profile_name", _fake_from_profile_name
    )
    return llm


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
) -> Iterator[TestClient]:
    """A TestClient over a real ``create_app()`` with a real service.

    ``KT_SESSION_DIR`` is redirected at ``tmp_path`` so persistence
    (saved-session list / resume / on-disk history) reads and writes
    the same isolated directory the engine saves into.

    NOTE: the identity LLM stores (``llm/api_keys.py``,
    ``llm/backends.py``, ``studio/identity/mcp_servers.py``,
    ``ui_prefs.py``, ``editors/skills_state.py``) bind
    ``Path.home() / ".kohakuterrarium"`` as an import-time constant
    and honour no env override — so this tier exercises only the
    *read* side of those routes plus error paths that 4xx before any
    write.  See the report's ``B-fat2-api`` notes.
    """
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))

    # Reset the process-wide catalog workspace before AND after so a
    # workspace opened mid-test never leaks into a sibling test.
    _catalog_deps.set_workspace(None)

    engine = Terrarium(session_dir=str(session_dir))
    service = LocalTerrariumService(engine)
    set_service(service)

    app = create_app()
    # ``with TestClient`` runs the lifespan — startup attaches the
    # runtime-graph prompt, shutdown drives ``engine.shutdown()``.
    with TestClient(app) as test_client:
        yield test_client

    set_service(None)
    _catalog_deps.set_workspace(None)


@pytest.fixture
def workspace_dir(tmp_path: Path, creature_dir: Path) -> Path:
    """A real on-disk Studio workspace root.

    ``LocalWorkspace.open`` expects a directory it can manage; the
    catalog routes (creatures / modules / schema / manifest /
    validate / skills) all operate relative to it.  The creature
    config dir is nested inside so workspace creature discovery has
    something to find.
    """
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def _stream_turn(ws, message: str) -> str:
    """Send one user input over an attached IO WebSocket and collect
    the streamed assistant text up to the post-turn ``idle`` frame."""
    ws.send_json({"type": "input", "content": message})
    chunks: list[str] = []
    while True:
        frame = ws.receive_json()
        if frame.get("type") == "text":
            chunks.append(frame["content"])
        elif frame.get("type") == "idle":
            break
        elif frame.get("type") == "error":
            raise AssertionError(f"WS chat error frame: {frame!r}")
    return "".join(chunks)


# ── workflows ─────────────────────────────────────────────────────────


class TestApiIntegration:
    """Fat end-to-end workflows over the real HTTP + WS API surface."""

    def test_creature_session_lifecycle_workflow(
        self,
        client: TestClient,
        creature_dir: Path,
        workspace_dir: Path,
        scripted_llm: ScriptedLLM,
    ) -> None:
        """Create → list → WS chat → settings round-trip → catalog /
        identity / metrics / attach surfaces → per-creature control →
        chat branches → history → delete.

        Mirrors ``agentAPI.create`` → ``sessionAPI.listActive`` → the
        ``/ws/sessions/.../chat`` attach → ``terrariumAPI`` settings
        calls → the catalog + settings panes the macro shell opens
        alongside a live creature → ``terrariumAPI.getHistory``.

        Also drives the Studio authoring surface a real client opens in
        a side panel — workspace open / creature + module scaffolding /
        schema / manifest sync / validate / skills / package browser —
        and the full identity write round-trip (backends / profiles /
        api-keys / MCP registry / default-model) the Settings page
        commits.
        """
        # ── boot-time read-only surfaces the frontend hits before any
        #    session exists: studio meta + catalog + identity. ─────────
        resp = client.get("/api/studio/meta/health")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        resp = client.get("/api/studio/meta/version")
        assert resp.status_code == 200
        version = resp.json()
        assert version["mode"] == "standalone"
        assert version["node_count"] == 1

        # Catalog: server-info, commands, builtin models, registry.
        resp = client.get("/api/configs/server-info")
        assert resp.status_code == 200
        assert resp.json()["cwd"] == os.getcwd()

        resp = client.get("/api/catalog/commands")
        assert resp.status_code == 200
        command_names = {c["name"] for c in resp.json()}
        # The built-in slash commands the user_command catalog ships.
        assert {"help", "status", "clear"}.issubset(command_names)

        resp = client.get("/api/catalog/builtins/tools")
        assert resp.status_code == 200
        tool_names = {t["name"] for t in resp.json()}
        assert "bash" in tool_names and "read" in tool_names

        resp = client.get("/api/catalog/builtins/subagents")
        assert resp.status_code == 200
        assert len(resp.json()) > 0

        # Built-in tool doc — present for a real tool, 404 for nonsense.
        resp = client.get("/api/catalog/builtins/tools/bash/doc")
        assert resp.status_code == 200
        assert resp.json()["name"] == "bash"
        resp = client.get("/api/catalog/builtins/tools/not_a_real_tool/doc")
        assert resp.status_code == 404

        resp = client.get("/api/registry/remote")
        assert resp.status_code == 200  # bundled registry.json always loads

        resp = client.get("/api/catalog/packages")
        assert resp.status_code == 200
        # Every entry is a well-formed package record (vacuously true
        # for an empty catalog — the installed set is environment state).
        assert all(isinstance(p, dict) and "name" in p for p in resp.json())

        # Catalog without an open workspace → catalog/creatures requires
        # one and 409s; the workspace-optional listers still answer.
        resp = client.get("/api/catalog/creatures")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "no_workspace"
        resp = client.get("/api/catalog/builtins/plugins")
        assert resp.status_code == 200  # workspace-optional → empty list ok

        # Templates list is static; render a known one, 404 an unknown.
        resp = client.get("/api/studio/templates")
        assert resp.status_code == 200
        template_ids = {t["id"] for t in resp.json()}
        assert "tool-minimal" in template_ids
        resp = client.post(
            "/api/studio/templates/render", json={"id": "no-such-template"}
        )
        assert resp.status_code == 404

        # Module-source validation — pure syntax check, no workspace.
        resp = client.post(
            "/api/studio/validate/module",
            json={"kind": "tools", "source": "def x(:\n"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        resp = client.post(
            "/api/studio/validate/module",
            json={"kind": "bogus_kind", "source": "x = 1"},
        )
        assert resp.status_code == 400

        # ── Studio authoring surface — the editor panel a real client
        #    opens beside a live session.  Open a real workspace, then
        #    drive creature + module scaffolding through to manifest
        #    sync, schema introspection, doc sidecars and validation. ──
        # Before opening: workspace-bound routes 409.
        resp = client.get("/api/studio/workspace")
        assert resp.status_code == 409
        # Opening a path that doesn't exist → documented 400.
        resp = client.post(
            "/api/studio/workspace/open",
            json={"path": str(workspace_dir / "does-not-exist")},
        )
        assert resp.status_code == 400
        # Open the real workspace root.
        resp = client.post(
            "/api/studio/workspace/open", json={"path": str(workspace_dir)}
        )
        assert resp.status_code == 200
        resp = client.get("/api/studio/workspace")
        assert resp.status_code == 200
        assert set(resp.json()) == {"creatures", "modules", "root"}

        # Creatures: empty, then scaffold one, then it lists + loads.
        resp = client.get("/api/studio/creatures")
        assert resp.status_code == 200
        assert resp.json() == []
        resp = client.post("/api/studio/creatures", json={"name": "wsbot"})
        assert resp.status_code == 201
        assert resp.json()["name"] == "wsbot"
        resp = client.get("/api/studio/creatures")
        assert resp.status_code == 200
        assert [c["name"] for c in resp.json()] == ["wsbot"]
        resp = client.get("/api/studio/creatures/wsbot")
        assert resp.status_code == 200
        assert resp.json()["name"] == "wsbot"
        # Re-scaffolding the same name → 409; loading a ghost → 404.
        resp = client.post("/api/studio/creatures", json={"name": "wsbot"})
        assert resp.status_code == 409
        resp = client.get("/api/studio/creatures/ghost-creature")
        assert resp.status_code == 404
        # Prompt-file round-trip: write then read back the exact bytes.
        resp = client.put(
            "/api/studio/creatures/wsbot/prompts/system.md",
            json={"content": "workspace bot prompt"},
        )
        assert resp.status_code == 200
        resp = client.get("/api/studio/creatures/wsbot/prompts/system.md")
        assert resp.status_code == 200
        assert resp.json()["content"] == "workspace bot prompt"
        resp = client.get("/api/studio/creatures/wsbot/prompts/missing.md")
        assert resp.status_code == 404

        # Modules: scaffold a tool, load it, save it, sync to manifest.
        resp = client.get("/api/studio/modules/tools")
        assert resp.status_code == 200
        assert resp.json() == []
        resp = client.get("/api/studio/modules/not-a-kind")
        assert resp.status_code == 400
        resp = client.post("/api/studio/modules/tools", json={"name": "wstool"})
        assert resp.status_code == 201
        assert resp.json()["name"] == "wstool"
        resp = client.get("/api/studio/modules/tools")
        assert resp.status_code == 200
        assert [m["name"] for m in resp.json()] == ["wstool"]
        resp = client.get("/api/studio/modules/tools/wstool")
        assert resp.status_code == 200
        loaded_module = resp.json()
        resp = client.put(
            "/api/studio/modules/tools/wstool",
            json={
                "mode": loaded_module["mode"],
                "form": loaded_module["form"],
                "execute_body": loaded_module["execute_body"],
                "raw_source": loaded_module["raw_source"],
            },
        )
        assert resp.status_code == 200
        # Loading a module that was never scaffolded → 404.
        resp = client.get("/api/studio/modules/tools/never_made")
        assert resp.status_code == 404
        # Module doc sidecar: missing first, then written + read back.
        resp = client.get("/api/studio/modules/tools/wstool/doc")
        assert resp.status_code == 200
        assert resp.json()["exists"] is False
        resp = client.put(
            "/api/studio/modules/tools/wstool/doc",
            json={"content": "# wstool docs"},
        )
        assert resp.status_code == 200
        assert resp.json()["exists"] is True
        resp = client.get("/api/studio/modules/tools/wstool/doc")
        assert resp.status_code == 200
        assert resp.json()["content"] == "# wstool docs"
        # Manifest sync — appends the module to kohaku.yaml; unknown
        # kind 400s, missing module 404s.
        resp = client.post(
            "/api/studio/workspace/manifest/sync",
            json={"kind": "tools", "name": "wstool"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        resp = client.post(
            "/api/studio/workspace/manifest/sync",
            json={"kind": "bogus", "name": "wstool"},
        )
        assert resp.status_code == 400
        resp = client.post(
            "/api/studio/workspace/manifest/sync",
            json={"kind": "tools", "name": "no_such_module"},
        )
        assert resp.status_code == 404

        # Module schema introspection — builtin tools schema is a pure
        # param list; a custom entry with no module path warns.
        resp = client.post(
            "/api/studio/module_schema",
            json={"kind": "tools", "type": "builtin"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json()["params"], list)
        resp = client.post(
            "/api/studio/module_schema",
            json={"kind": "tools", "type": "custom"},
        )
        assert resp.status_code == 200
        warnings = resp.json()["warnings"]
        assert any(w["code"] == "missing_module" for w in warnings)

        # Creature-config validation — a clean config passes, a bad one
        # surfaces a typed schema error.
        resp = client.post(
            "/api/studio/validate/creature",
            json={"config": {"name": "ok", "system_prompt": "hi"}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        resp = client.post(
            "/api/studio/validate/creature",
            json={"config": {"name": 123}},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

        # Skills discovery — walks the workspace cwd; toggling a ghost
        # skill 404s.
        resp = client.get("/api/studio/skills")
        assert resp.status_code == 200
        # Each discovered skill is a well-formed named record.
        assert all(isinstance(s, dict) and "name" in s for s in resp.json())
        resp = client.post("/api/studio/skills/no-such-skill/toggle")
        assert resp.status_code == 404

        # Package browser — ``kt-biome`` ships with the repo, so its
        # summary + extension lists resolve; an uninstalled name 404s.
        resp = client.get("/api/studio/packages")
        assert resp.status_code == 200
        package_names = {p["name"] for p in resp.json()}
        assert "kt-biome" in package_names
        resp = client.get("/api/studio/packages/kt-biome")
        assert resp.status_code == 200
        biome = resp.json()
        assert biome["name"] == "kt-biome"
        assert biome["tools"] >= 1
        resp = client.get("/api/studio/packages/kt-biome/creatures")
        assert resp.status_code == 200
        assert any(c["name"] == "general" for c in resp.json())
        resp = client.get("/api/studio/packages/kt-biome/tools")
        assert resp.status_code == 200
        # kt-biome ships tools (``biome["tools"] >= 1`` above) — the
        # per-package tool list surfaces them as named records.
        assert resp.json() and all("name" in t for t in resp.json())
        resp = client.get("/api/studio/packages/kt-biome/plugins")
        assert resp.status_code == 200
        resp = client.get("/api/studio/packages/kt-biome/triggers")
        assert resp.status_code == 200
        resp = client.get("/api/studio/packages/kt-biome/io")
        assert resp.status_code == 200
        resp = client.get("/api/studio/packages/kt-biome/skills")
        assert resp.status_code == 200
        resp = client.get("/api/studio/packages/kt-biome/modules/tools")
        assert resp.status_code == 200
        resp = client.get("/api/studio/packages/no-such-package")
        assert resp.status_code == 404
        resp = client.get("/api/studio/packages/no-such-package/creatures")
        assert resp.status_code == 404
        resp = client.get("/api/studio/packages/no-such-package/tools")
        assert resp.status_code == 404

        # Catalog builtins doc — a real subagent has a doc; nonsense 404s.
        resp = client.get("/api/catalog/builtins/subagents")
        assert resp.status_code == 200
        a_subagent = resp.json()[0]["name"]
        resp = client.get(f"/api/catalog/builtins/subagents/{a_subagent}/doc")
        assert resp.status_code == 200
        assert resp.json()["name"] == a_subagent
        resp = client.get("/api/catalog/builtins/subagents/not_a_subagent/doc")
        assert resp.status_code == 404

        # With a workspace open, ``catalog/creatures`` answers (it 409'd
        # earlier). The scaffolded ``wsbot`` is discoverable.
        resp = client.get("/api/catalog/creatures")
        assert resp.status_code == 200
        assert any(c["name"] == "wsbot" for c in resp.json())

        # Close the workspace; the bound routes 409 again.
        resp = client.post("/api/studio/workspace/close")
        assert resp.status_code == 204
        resp = client.get("/api/studio/workspace")
        assert resp.status_code == 409

        # Identity surfaces the Settings page reads on open.
        resp = client.get("/api/settings/backends")
        assert resp.status_code == 200
        assert "backends" in resp.json()
        resp = client.get("/api/settings/profiles")
        assert resp.status_code == 200
        assert "profiles" in resp.json()
        resp = client.get("/api/settings/keys")
        assert resp.status_code == 200
        assert "providers" in resp.json()
        resp = client.get("/api/settings/native-tools")
        assert resp.status_code == 200
        assert "tools" in resp.json()
        resp = client.get("/api/settings/default-model")
        assert resp.status_code == 200
        assert "default_model" in resp.json()
        resp = client.get("/api/settings/models")
        assert resp.status_code == 200
        resp = client.get("/api/settings/codex-status")
        assert resp.status_code == 200

        # Identity write path: deleting a backend that does not exist
        # is a documented 404, not a silent success.
        resp = client.delete("/api/settings/backends/does-not-exist")
        assert resp.status_code == 404
        # Deleting a profile / api-key / MCP server that does not exist
        # likewise 404s — these error branches resolve before any
        # write touches the (unrelocatable) real config tree.
        resp = client.delete("/api/settings/profiles/ghost-provider/ghost-name")
        assert resp.status_code == 404
        resp = client.delete("/api/settings/keys/definitely-not-a-provider")
        assert resp.status_code == 404
        resp = client.delete("/api/settings/mcp/definitely-not-a-server")
        assert resp.status_code == 404
        # Creating a profile against a provider that does not exist is a
        # documented 404 — the lookup fails before the YAML write.
        resp = client.post(
            "/api/settings/profiles",
            json={"name": "x", "model": "m", "provider": "no-such-provider-xyz"},
        )
        assert resp.status_code == 404
        # MCP registry read round-trips.
        resp = client.get("/api/settings/mcp")
        assert resp.status_code == 200
        assert "servers" in resp.json()

        # Process metrics snapshot — no sessions yet, so the gauges
        # report zero running creatures.
        resp = client.get("/api/metrics/snapshot")
        assert resp.status_code == 200
        snapshot = resp.json()
        assert snapshot["gauges"]["sessions_open"] == 0
        assert snapshot["gauges"]["creatures_running"] == 0

        # 1. POST create a creature session (frontend: agentAPI.create).
        resp = client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        created = resp.json()
        assert created["status"] == "running"
        session_id = created["session_id"]
        creature_id = created["agent_id"]
        assert session_id and creature_id

        # Creating from a path that does not exist is a documented 400.
        resp = client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature_dir / "nope")},
        )
        assert resp.status_code == 400

        # 2. GET it appears in the canonical active-session list.
        resp = client.get("/api/sessions/active")
        assert resp.status_code == 200
        active = resp.json()
        assert [s["session_id"] for s in active] == [session_id]

        # And the unified getter resolves it by creature_id too.
        resp = client.get(f"/api/sessions/active/{creature_id}")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == session_id

        # Legacy ``/agents`` shims still serve the historical shape — a
        # solo session shows up in the legacy agent list and the
        # legacy per-id accessor resolves it.
        resp = client.get("/api/sessions/active/agents")
        assert resp.status_code == 200
        legacy_agents = resp.json()
        assert [a["agent_id"] for a in legacy_agents] == [creature_id]
        resp = client.get(f"/api/sessions/active/agents/{creature_id}")
        assert resp.status_code == 200
        assert resp.json()["graph_id"] == session_id
        # A solo session is NOT a terrarium → legacy terrarium list empty.
        resp = client.get("/api/sessions/active/terrariums")
        assert resp.status_code == 200
        assert resp.json() == []
        # Unknown id on the unified + legacy getters → 404.
        assert client.get("/api/sessions/active/ghost-id").status_code == 404
        assert client.get("/api/sessions/active/agents/ghost-id").status_code == 404

        # Metrics now reports one running solo creature.
        resp = client.get("/api/metrics/snapshot")
        assert resp.status_code == 200
        assert resp.json()["gauges"]["creatures_running"] == 1

        # Attach-policy hints — the Inspector Overview "IO bindings"
        # line. A live creature + session both report a policy list;
        # an unknown id 404s (the frontend silently omits the line).
        resp = client.get(f"/api/attach/policies/{creature_id}")
        assert resp.status_code == 200
        assert isinstance(resp.json()["policies"], list)
        resp = client.get(f"/api/attach/session_policies/{session_id}")
        assert resp.status_code == 200
        assert isinstance(resp.json()["policies"], list)
        assert client.get("/api/attach/policies/ghost-id").status_code == 404
        assert client.get("/api/attach/session_policies/ghost-id").status_code == 404

        # 3. Open the IO WebSocket and stream one real turn. The
        #    scripted reply must stream back over the socket.
        ws_url = f"/ws/sessions/{session_id}/creatures/{creature_id}/chat"
        with client.websocket_connect(ws_url) as ws:
            info = ws.receive_json()
            assert info["activity_type"] == "session_info"
            assert info["agent_name"] == "alice"
            reply = _stream_turn(ws, "hello creature")
        assert reply == _REPLY_ONE
        assert scripted_llm.call_count == 1

        # 3b. The HTTP chat-mutation surface the frontend falls back to
        #     when the WS is down — a second turn, then regenerate /
        #     edit / rewind against it. The chat surface is exercised
        #     first, then the per-creature settings pane below; the
        #     scripted seam now also covers ``switch_model`` (see the
        #     ``scripted_llm`` fixture), so a turn after the model
        #     switch stays deterministic too.
        base = f"/api/sessions/{session_id}/creatures/{creature_id}"
        resp = client.post(f"{base}/chat", json={"message": "a second http turn"})
        assert resp.status_code == 200
        assert resp.json() == {"response": _REPLY_TWO}
        assert scripted_llm.call_count == 2
        # History now carries both turns — the WS turn and this one —
        # with the streamed scripted replies.
        resp = client.get(f"{base}/history")
        assert resp.status_code == 200
        messages = resp.json().get("messages", [])
        roles = [m.get("role") for m in messages]
        assert "user" in roles and "assistant" in roles
        user_msgs = [m for m in messages if m.get("role") == "user"]
        assert len(user_msgs) == 2
        joined = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in messages
        )
        assert "hello creature" in joined
        assert "a second http turn" in joined
        assert _REPLY_ONE in joined
        # Regenerate the conversation tail — fires another LLM call.
        resp = client.post(f"{base}/regenerate", json={})
        assert resp.status_code == 200
        assert resp.json() == {"status": "regenerating", "turn_index": None}
        # Edit the first user message in place and re-run from there.
        resp = client.post(
            f"{base}/messages/0/edit",
            json={"content": "edited first turn", "user_position": 0},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "edited"
        # Editing a target that isn't a user message → documented 400.
        resp = client.post(
            f"{base}/messages/999/edit",
            json={"content": "x", "user_position": 999},
        )
        assert resp.status_code == 400
        # Rewind the conversation to message index 1 (keeps the system
        # message at index 0 intact).
        resp = client.post(f"{base}/messages/1/rewind")
        assert resp.status_code == 200
        assert resp.json() == {"status": "rewound"}
        # The system prompt survives a rewind to a non-zero index.
        resp = client.get(f"{base}/system-prompt")
        assert resp.status_code == 200
        assert "integration-test creature" in str(resp.json())
        # Regression guard for B-fat2-api-2 (FIXED): rewinding to message
        # index 0 must NOT drop the leading system message. The rewind
        # clamps past leading system message(s) (see
        # ``core.conversation.Conversation.truncate_from``), so a rewind
        # to a fresh conversation still carries the configured system
        # prompt. Before the fix ``get_system_prompt`` returned an empty
        # ``{"text": ""}`` here.
        resp = client.post(f"{base}/messages/0/rewind")
        assert resp.status_code == 200
        resp = client.get(f"{base}/system-prompt")
        assert resp.status_code == 200
        assert "integration-test creature" in str(resp.json())

        # 4. Per-creature settings round-trip — change, then read back.
        # 4a. Model switch (frontend: terrariumAPI.switchCreatureModel).
        #     ``switch_model`` validates against the real preset table,
        #     so the target must be a genuine profile selector.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/model",
            json={"model": "openai/gpt-4o-mini"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "switched", "model": "openai/gpt-4o-mini"}
        # Read-back: the active-session getter reflects the switch via
        # the creature's canonical ``llm_name`` identifier.
        resp = client.get(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["creatures"][0]["llm_name"] == "openai/gpt-4o-mini"
        # A chat turn AFTER the model switch still runs: ``switch_model``
        # rebinds the provider via ``create_llm_from_profile_name``,
        # which the ``scripted_llm`` fixture also seams — so the switched
        # creature stays deterministic and produces a real reply.
        calls_before_switch_turn = scripted_llm.call_count
        resp = client.post(f"{base}/chat", json={"message": "post-switch turn"})
        assert resp.status_code == 200
        assert resp.json() == {"response": _REPLY_TWO}
        assert scripted_llm.call_count == calls_before_switch_turn + 1
        # Switching to a bogus model is a documented 400.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/model",
            json={"model": "not/a/real/model"},
        )
        assert resp.status_code == 400
        # Model switch on an unknown creature → 404.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/ghost/model",
            json={"model": "openai/gpt-4o-mini"},
        )
        assert resp.status_code == 404

        # 4b. Scratchpad patch (frontend: terrariumAPI.patchScratchpad).
        resp = client.patch(
            f"/api/sessions/{session_id}/creatures/{creature_id}/scratchpad",
            json={"updates": {"focus": "integration testing"}},
        )
        assert resp.status_code == 200
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/{creature_id}/scratchpad"
        )
        assert resp.status_code == 200
        assert resp.json().get("focus") == "integration testing"
        # Scratchpad on an unknown creature → 404.
        resp = client.get(f"/api/sessions/{session_id}/creatures/ghost/scratchpad")
        assert resp.status_code == 404

        # 4c. The rest of the per-creature state pane — triggers / env /
        #     system prompt / working dir / native-tool options. The
        #     creature config declares no triggers, so the list is
        #     empty; env + system prompt are always populated. (``base``
        #     was defined at step 3b above.)
        resp = client.get(f"{base}/triggers")
        assert resp.status_code == 200
        assert resp.json() == []
        resp = client.get(f"{base}/env")
        assert resp.status_code == 200
        # ``/env`` reports the creature's real working dir + redacted env.
        assert resp.json()["pwd"] and "env" in resp.json()
        # NOTE: ``system-prompt`` was already exercised in step 3b
        # (the populated read, and the post-rewind-to-0 read that
        # confirms the prompt survives — B-fat2-api-2) — not re-asserted
        # here.

        resp = client.get(f"{base}/working-dir")
        assert resp.status_code == 200
        original_pwd = resp.json()["pwd"]
        assert original_pwd
        # Set the working dir to a real directory and read it back.
        resp = client.put(f"{base}/working-dir", json={"path": str(creature_dir)})
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"
        resp = client.get(f"{base}/working-dir")
        assert resp.status_code == 200
        assert Path(resp.json()["pwd"]) == creature_dir.resolve()

        # Setting the working dir to a path that does not exist is a
        # documented 400; an unknown creature 404s on both verbs.
        resp = client.put(
            f"{base}/working-dir", json={"path": str(creature_dir / "nope")}
        )
        assert resp.status_code == 400
        resp = client.get(f"/api/sessions/{session_id}/creatures/ghost/working-dir")
        assert resp.status_code == 404
        resp = client.put(
            f"/api/sessions/{session_id}/creatures/ghost/working-dir",
            json={"path": str(creature_dir)},
        )
        assert resp.status_code == 404
        # env / triggers / system-prompt on an unknown creature → 404.
        assert (
            client.get(f"/api/sessions/{session_id}/creatures/ghost/env").status_code
            == 404
        )
        assert (
            client.get(
                f"/api/sessions/{session_id}/creatures/ghost/triggers"
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/sessions/{session_id}/creatures/ghost/system-prompt"
            ).status_code
            == 404
        )

        resp = client.get(f"{base}/native-tool-options")
        assert resp.status_code == 200
        # No native-tool backend is configured for this creature, so the
        # inventory of provider-native tools is empty.
        assert resp.json()["tools"] == []
        # A no-op set (empty values) is accepted and echoed back; a set
        # carrying an unknown option key is rejected with a 400.
        resp = client.put(
            f"{base}/native-tool-options",
            json={"tool": "web_search", "values": {}},
        )
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "saved",
            "tool": "web_search",
            "values": {},
        }
        resp = client.put(
            f"{base}/native-tool-options",
            json={"tool": "web_search", "values": {"bogus_option": 1}},
        )
        assert resp.status_code == 400
        # An unknown creature 404s on both verbs.
        resp = client.put(
            f"/api/sessions/{session_id}/creatures/ghost/native-tool-options",
            json={"tool": "web_search", "values": {}},
        )
        assert resp.status_code == 404
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/ghost/native-tool-options"
        )
        assert resp.status_code == 404

        # 4d. Per-creature modules + plugins panes.
        resp = client.get(f"{base}/modules")
        assert resp.status_code == 200
        modules = resp.json()["modules"]
        # The creature ships the built-in cross-cutting plugins
        # (sandbox / budget / permgate / compact.auto) as configurable
        # modules — all disabled by default.
        module_names = {m["name"] for m in modules}
        assert {"sandbox", "budget"}.issubset(module_names)
        resp = client.get(f"/api/sessions/{session_id}/creatures/ghost/modules")
        assert resp.status_code == 404

        # Module-options round-trip on the real ``sandbox`` plugin —
        # read defaults, write a new backend value, read it back.
        resp = client.get(f"{base}/modules/plugin/sandbox/options")
        assert resp.status_code == 200
        assert resp.json()["options"]["backend"] == "auto"
        resp = client.put(
            f"{base}/modules/plugin/sandbox/options",
            json={"values": {"backend": "audit"}},
        )
        assert resp.status_code == 200
        assert resp.json()["options"]["backend"] == "audit"
        resp = client.get(f"{base}/modules/plugin/sandbox/options")
        assert resp.status_code == 200
        assert resp.json()["options"]["backend"] == "audit"
        # Options for a module that isn't on the creature → 404.
        resp = client.get(f"{base}/modules/plugin/no_such_plugin/options")
        assert resp.status_code == 404
        resp = client.put(
            f"{base}/modules/plugin/no_such_plugin/options",
            json={"values": {}},
        )
        assert resp.status_code == 404

        # Toggle the real ``sandbox`` plugin off→on→off through the
        # unified module-toggle route; the modules list reflects it.
        resp = client.post(f"{base}/modules/plugin/sandbox/toggle")
        assert resp.status_code == 200
        resp = client.get(f"{base}/modules")
        sandbox_entry = next(
            m for m in resp.json()["modules"] if m["name"] == "sandbox"
        )
        assert sandbox_entry["enabled"] is True
        resp = client.post(f"{base}/modules/plugin/sandbox/toggle")
        assert resp.status_code == 200
        resp = client.get(f"{base}/modules")
        sandbox_entry = next(
            m for m in resp.json()["modules"] if m["name"] == "sandbox"
        )
        assert sandbox_entry["enabled"] is False

        resp = client.get(f"{base}/plugins")
        assert resp.status_code == 200
        # The creature carries the builtin ``sandbox`` plugin (toggled
        # through ``/modules`` just above) — it shows in the plugin list.
        assert any(p["name"] == "sandbox" for p in resp.json())
        # Toggling a module that isn't on the creature → 400: the
        # creature resolves, but the module name doesn't (ValueError
        # in ``service.toggle_module`` → documented 400).
        resp = client.post(f"{base}/modules/tools/no_such_tool/toggle")
        assert resp.status_code == 400
        # Toggling a module on an unknown creature → 404.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/ghost/modules/plugin/sandbox/toggle"
        )
        assert resp.status_code == 404
        # Toggling a plugin that isn't loaded → 404.
        resp = client.post(f"{base}/plugins/no_such_plugin/toggle")
        assert resp.status_code == 404

        # 4e. Per-creature control surface — jobs list (empty after a
        #     direct turn), interrupt (idempotent), cancel of a missing
        #     job → 404.
        resp = client.get(f"{base}/jobs")
        assert resp.status_code == 200
        # No tool / sub-agent jobs remain after a completed direct turn.
        assert resp.json() == []
        resp = client.post(f"{base}/interrupt")
        assert resp.status_code == 200
        assert resp.json() == {"status": "interrupted"}
        resp = client.post(f"{base}/tasks/no-such-job/stop")
        assert resp.status_code == 404
        resp = client.post(f"{base}/promote/no-such-job")
        assert resp.status_code == 200
        assert resp.json() == {"status": "not_found"}

        # 4f. Slash-command execution — ``status`` is a real builtin.
        resp = client.post(f"{base}/command", json={"command": "status", "args": ""})
        assert resp.status_code == 200

        # Every chat-mutation + control route 404s on an unknown
        # creature — the resolver can't find it, so every verb 404s.
        ghost = f"/api/sessions/{session_id}/creatures/ghost"
        assert client.post(f"{ghost}/chat", json={"message": "x"}).status_code == 404
        assert client.post(f"{ghost}/regenerate", json={}).status_code == 404
        assert (
            client.post(
                f"{ghost}/messages/0/edit", json={"content": "x", "user_position": 0}
            ).status_code
            == 404
        )
        assert client.post(f"{ghost}/messages/0/rewind").status_code == 404
        assert client.get(f"{ghost}/history").status_code == 404
        assert client.get(f"{ghost}/branches").status_code == 404
        assert client.post(f"{ghost}/interrupt").status_code == 404
        assert client.get(f"{ghost}/jobs").status_code == 404
        assert client.post(f"{ghost}/tasks/j/stop").status_code == 404
        assert client.post(f"{ghost}/promote/j").status_code == 404
        assert (
            client.post(
                f"{ghost}/command", json={"command": "status", "args": ""}
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"{ghost}/scratchpad", json={"updates": {"k": "v"}}
            ).status_code
            == 404
        )

        # 5. GET history — still reachable after the chat mutations.
        #    The rewind-to-0 in step 3b truncated the live conversation
        #    back to just the system prompt; the only user turn since is
        #    the single "post-switch turn" chat driven in step 4a. (The
        #    full pre-rewind transcript was asserted in step 3b.)
        resp = client.get(f"{base}/history")
        assert resp.status_code == 200
        post_rewind_users = [
            m for m in resp.json().get("messages", []) if m.get("role") == "user"
        ]
        assert [m.get("content") for m in post_rewind_users] == ["post-switch turn"]

        # Branches view — after regenerate + edit opened new branches,
        # the branch state route answers cleanly.
        resp = client.get(f"{base}/branches")
        assert resp.status_code == 200

        # 5b. Rename the creature through the per-session rename route
        #     (mounted under ``/api/sessions/active``).
        resp = client.post(
            f"/api/sessions/active/{session_id}/creatures/{creature_id}/rename",
            json={"name": "alice-renamed"},
        )
        assert resp.status_code == 200
        # The active session now reflects the new display name.
        resp = client.get(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["creatures"][0]["name"] == "alice-renamed"
        # Renaming an unknown creature → 404.
        resp = client.post(
            f"/api/sessions/active/{session_id}/creatures/ghost/rename",
            json={"name": "x"},
        )
        assert resp.status_code == 404

        # 5c. The canonical + legacy creation aliases the frontend still
        #     calls. ``/creature`` is the new canonical solo-session
        #     route; ``/agents/{id}/rename`` + ``DELETE /agents/{id}``
        #     are the legacy shims a bookmarked client hits.
        resp = client.post(
            "/api/sessions/active/creature",
            json={"config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        alias_body = resp.json()
        assert alias_body["status"] == "running"
        alias_sid = alias_body["session_id"]
        alias_cid = alias_body["creatures"][0]["creature_id"]
        # ``/creature`` from a bad path → documented 400.
        resp = client.post(
            "/api/sessions/active/creature",
            json={"config_path": str(creature_dir / "nope")},
        )
        assert resp.status_code == 400
        # ``/terrarium`` from a bad recipe path → 400.
        resp = client.post(
            "/api/sessions/active/terrarium",
            json={"config_path": str(creature_dir / "nope")},
        )
        assert resp.status_code == 400
        # Legacy ``/terrariums`` create alias from a bad path → 400 too.
        resp = client.post(
            "/api/sessions/active/terrariums",
            json={"config_path": str(creature_dir / "nope")},
        )
        assert resp.status_code == 400
        # Legacy per-creature rename shim.
        resp = client.post(
            f"/api/sessions/active/agents/{alias_cid}/rename",
            json={"name": "alias-renamed"},
        )
        assert resp.status_code == 200
        resp = client.get(f"/api/sessions/active/agents/{alias_cid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "alias-renamed"
        # Renaming an unknown creature through the legacy shim → 404.
        resp = client.post(
            "/api/sessions/active/agents/ghost-id/rename", json={"name": "x"}
        )
        assert resp.status_code == 404
        # Renaming an unknown session through the legacy terrarium shim → 404.
        resp = client.post(
            "/api/sessions/active/terrariums/ghost-sid/rename", json={"name": "x"}
        )
        assert resp.status_code == 404
        # Legacy ``DELETE /agents/{id}`` stops the solo session.
        resp = client.delete(f"/api/sessions/active/agents/{alias_cid}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "stopped"}
        assert alias_sid not in {
            s["session_id"] for s in client.get("/api/sessions/active").json()
        }
        # Deleting it again through the legacy shim → 404.
        resp = client.delete(f"/api/sessions/active/agents/{alias_cid}")
        assert resp.status_code == 404
        # Legacy ``DELETE /terrariums/{id}`` on an unknown id → 404.
        resp = client.delete("/api/sessions/active/terrariums/ghost-sid")
        assert resp.status_code == 404

        # 6. DELETE the session — it leaves the active list.
        resp = client.delete(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "stopped"}
        assert client.get("/api/sessions/active").json() == []
        # Deleting it again → 404.
        resp = client.delete(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 404

    def test_session_persistence_and_resume_workflow(
        self, client: TestClient, creature_dir: Path, scripted_llm: ScriptedLLM
    ) -> None:
        """Run a turn → saved-session list → persistence viewer (tree /
        summary / turns / events / export / history / memory search /
        fork) → stop → resume → delete.

        Mirrors the ``sessionAPI`` saved-session surface plus the
        Session Viewer's read-only endpoints: ``list`` / ``getHistory``
        / ``getTree`` / ``getSummary`` / ``getTurns`` / ``getEvents`` /
        ``export`` / ``searchSession`` / ``fork`` / ``resume`` /
        ``delete``.
        """
        # Create + run a turn so the .kohakutr session store fills.
        resp = client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        created = resp.json()
        session_id = created["session_id"]
        creature_id = created["agent_id"]

        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/chat",
            json={"message": "persist this turn"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"response": _REPLY_ONE}

        # A second turn so the viewer has more than one turn to page.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/chat",
            json={"message": "second turn please"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"response": _REPLY_TWO}

        # Saved-session list (frontend: sessionAPI.list with refresh).
        resp = client.get("/api/sessions", params={"refresh": "true"})
        assert resp.status_code == 200
        listing = resp.json()
        assert listing["total"] == 1
        saved_name = listing["sessions"][0]["name"]
        assert "alice" in listing["sessions"][0]["agents"]

        # Saved-session aggregates — disk usage + stats both read the
        # same cached index the rail loads.
        resp = client.get("/api/sessions/disk-usage")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1
        resp = client.get("/api/sessions/stats")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

        # Search with pagination + a non-matching query.
        resp = client.get("/api/sessions", params={"search": "alice", "limit": 5})
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        resp = client.get("/api/sessions", params={"search": "zzz-no-match-zzz"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        # Pagination offset past the end → still 200, empty page, the
        # total unchanged (the index has exactly one saved session).
        resp = client.get("/api/sessions", params={"limit": 5, "offset": 50})
        assert resp.status_code == 200
        paged = resp.json()
        assert paged["total"] == 1
        assert paged["sessions"] == []
        assert paged["offset"] == 50
        # The same router is also mounted under the per-concern
        # ``/api/persistence/saved`` prefix — identical payload.
        resp = client.get("/api/persistence/saved")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        # While the session is still live, its per-creature HTTP
        # history carries both turns — and the ``ch:`` channel-history
        # branch of the same route answers for a (here empty) channel.
        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/history")
        assert resp.status_code == 200
        live_blob = str(resp.json())
        assert "persist this turn" in live_blob
        assert "second turn please" in live_blob
        resp = client.get(
            f"/api/sessions/{session_id}/creatures/ch:no-such-channel/history"
        )
        assert resp.status_code == 200

        # On-disk history index + per-target history read.
        resp = client.get(f"/api/sessions/{saved_name}/history")
        assert resp.status_code == 200
        targets = resp.json()
        assert "alice" in targets.get("targets", [])

        resp = client.get(f"/api/sessions/{saved_name}/history/alice")
        assert resp.status_code == 200
        disk_history = resp.json()
        disk_blob = str(disk_history)
        assert "persist this turn" in disk_blob
        assert _REPLY_ONE in disk_blob

        # History for a missing session → 404; the index + per-target
        # routes are also reachable under the ``/api/persistence/history``
        # prefix with the identical payload.
        assert client.get("/api/sessions/no-such-session/history").status_code == 404
        assert (
            client.get("/api/persistence/history/no-such-session/history").status_code
            == 404
        )
        resp = client.get(f"/api/persistence/history/{saved_name}/history")
        assert resp.status_code == 200
        assert "alice" in resp.json().get("targets", [])
        resp = client.get(f"/api/persistence/history/{saved_name}/history/alice")
        assert resp.status_code == 200
        assert "persist this turn" in str(resp.json())

        # Artifacts route — the session has no artifacts directory, so
        # any file path 404s (the path-resolution guard rejects it
        # before a FileResponse is built).
        resp = client.get(f"/api/sessions/{saved_name}/artifacts/nope.png")
        assert resp.status_code == 404

        # ── Session Viewer read-only surface (V1+V6 waves) ────────────
        resp = client.get(f"/api/sessions/{saved_name}/tree")
        assert resp.status_code == 200
        assert "alice" in str(resp.json())

        resp = client.get(f"/api/sessions/{saved_name}/summary")
        assert resp.status_code == 200
        summary = resp.json()
        # The summary names the saved creature and carries a totals block.
        assert "alice" in summary["agents"]
        assert "tool_calls" in summary["totals"]
        # Summary scoped to a specific agent still reports that agent.
        resp = client.get(
            f"/api/sessions/{saved_name}/summary", params={"agent": "alice"}
        )
        assert resp.status_code == 200
        assert "alice" in resp.json()["agents"]

        resp = client.get(
            f"/api/sessions/{saved_name}/turns", params={"agent": "alice"}
        )
        assert resp.status_code == 200
        turns_payload = resp.json()
        # Two chat turns were recorded; the turns index reports both.
        assert turns_payload["total"] == 2
        assert turns_payload["agent"] == "alice"
        assert [t["turn_index"] for t in turns_payload["turns"]] == [1, 2]
        # Range-scoped turns — only the second turn falls in [2, 2].
        resp = client.get(
            f"/api/sessions/{saved_name}/turns",
            params={"agent": "alice", "from_turn": 2, "to_turn": 2},
        )
        assert resp.status_code == 200
        assert [t["turn_index"] for t in resp.json()["turns"]] == [2]
        # Aggregate view collapses the per-turn rows; still 200.
        resp = client.get(
            f"/api/sessions/{saved_name}/turns",
            params={"agent": "alice", "aggregate": "true"},
        )
        assert resp.status_code == 200

        resp = client.get(
            f"/api/sessions/{saved_name}/events", params={"agent": "alice"}
        )
        assert resp.status_code == 200
        # Two real chat turns were recorded — the event log is non-empty
        # and ``count`` matches the returned event list length.
        events_payload = resp.json()
        assert events_payload["count"] == len(events_payload["events"]) >= 1
        # Events filtered to turn 1 + an explicit limit — the filter is
        # applied: every returned event belongs to turn 1.
        resp = client.get(
            f"/api/sessions/{saved_name}/events",
            params={"agent": "alice", "turn_index": 1, "limit": 10},
        )
        assert resp.status_code == 200
        assert resp.json()["filters"]["turn_index"] == 1
        assert all(e.get("turn_index") == 1 for e in resp.json()["events"])

        # Export — markdown transcript streams back as an attachment.
        resp = client.get(f"/api/sessions/{saved_name}/export", params={"format": "md"})
        assert resp.status_code == 200
        assert "persist this turn" in resp.text
        assert "attachment" in resp.headers.get("content-disposition", "")
        # JSONL export carries the same turn content, different mime.
        resp = client.get(
            f"/api/sessions/{saved_name}/export", params={"format": "jsonl"}
        )
        assert resp.status_code == 200
        assert "persist this turn" in resp.text

        # Diff against a missing other session → 404; a self-diff is a
        # valid request and — diffing a session against itself — reports
        # the two sides identical with no divergence.
        resp = client.get(f"/api/sessions/{saved_name}/diff", params={"other": "nope"})
        assert resp.status_code == 404
        resp = client.get(
            f"/api/sessions/{saved_name}/diff", params={"other": saved_name}
        )
        assert resp.status_code == 200
        diff_payload = resp.json()
        assert diff_payload["identical"] is True
        assert diff_payload["a_only"] == [] and diff_payload["b_only"] == []

        # Viewer nouns for a missing session → 404.
        assert client.get("/api/sessions/nope/tree").status_code == 404
        assert client.get("/api/sessions/nope/summary").status_code == 404
        assert client.get("/api/sessions/nope/turns").status_code == 404
        assert client.get("/api/sessions/nope/events").status_code == 404
        assert (
            client.get("/api/sessions/nope/export", params={"format": "md"}).status_code
            == 404
        )
        # The viewer router is also mounted under ``/api/persistence/viewer``.
        resp = client.get(f"/api/persistence/viewer/{saved_name}/tree")
        assert resp.status_code == 200
        assert "alice" in str(resp.json())

        # Memory search over the saved session — FTS finds the turn we
        # wrote ("persist this turn"); ``count`` matches the result list.
        resp = client.get(
            f"/api/sessions/{saved_name}/memory/search",
            params={"q": "persist", "mode": "fts"},
        )
        assert resp.status_code == 200
        search_payload = resp.json()
        assert search_payload["count"] == len(search_payload["results"]) >= 1
        resp = client.get(
            "/api/sessions/no-such-session/memory/search", params={"q": "x"}
        )
        assert resp.status_code == 404

        # Fork the saved session at event 1 into a new .kohakutr.
        resp = client.post(f"/api/sessions/{saved_name}/fork", json={"at_event_id": 1})
        assert resp.status_code == 201
        fork_body = resp.json()
        assert fork_body["fork_point"] == 1
        assert fork_body["session_id"]
        # Fork of a missing session → 404.
        resp = client.post(
            "/api/sessions/no-such-session/fork", json={"at_event_id": 1}
        )
        assert resp.status_code == 404
        # The forked file shows up in the saved listing on rebuild.
        resp = client.get("/api/sessions", params={"refresh": "true"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

        # Stop the live session first — on Windows the SQLite handle
        # must be released before resume re-opens the same file.
        resp = client.delete(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert client.get("/api/sessions/active").json() == []

        # Resume from disk (frontend: sessionAPI.resume). The URL slot
        # carries the saved file stem; the response's ``session_name``
        # is the creature's display name ("alice"), not the stem.
        resp = client.post(f"/api/sessions/{saved_name}/resume")
        assert resp.status_code == 200
        resumed = resp.json()
        assert resumed["type"] == "agent"
        assert resumed["session_name"] == "alice"
        resumed_id = resumed["instance_id"]

        # Resuming a session that does not exist on disk → 404.
        resp = client.post("/api/sessions/no-such-session/resume")
        assert resp.status_code == 404

        # The resumed session is live again in the active list.
        resp = client.get("/api/sessions/active")
        assert resp.status_code == 200
        assert [s["session_id"] for s in resp.json()] == [resumed_id]

        # Stop it, then DELETE the saved file off disk.
        resp = client.delete(f"/api/sessions/active/{resumed_id}")
        assert resp.status_code == 200

        resp = client.delete(f"/api/sessions/{saved_name}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        # Deleting a name that never existed → 404.
        resp = client.delete("/api/sessions/definitely-not-a-session-xyz")
        assert resp.status_code == 404
        # Gone from the saved-session listing after a forced rebuild
        # (the fork file remains).
        resp = client.get("/api/sessions", params={"refresh": "true"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_terrarium_hotplug_and_runtime_graph_workflow(
        self, client: TestClient, creature_dir: Path, scripted_llm: ScriptedLLM
    ) -> None:
        """Hot-plug a creature → declare + wire + send to a shared
        channel → output-wiring CRUD → runtime-graph snapshot over HTTP
        and over four WS streams (graph / observer / logs / files) →
        lab-only ``/api/nodes`` 404s in standalone mode.

        Mirrors the graph editor's data layer: ``runtimeGraphAPI`` plus
        the ``/ws/runtime/graph`` stream, the macro shell's hot-plug +
        channel-send + wiring calls, and the inspector's log / file /
        observer streams.
        """
        # Lab-only node routes 404 in standalone mode — fail loud, not
        # silent. Checked before any session so the 404 is unambiguous.
        assert client.get("/api/nodes").status_code == 404
        assert client.get("/api/nodes/_host/status").status_code == 404

        # Start a one-creature session.
        resp = client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]
        alice_id = resp.json()["agent_id"]

        # Hot-plug a second creature into the SAME session.
        resp = client.post(
            f"/api/sessions/active/{session_id}/creatures",
            json={"name": "bob", "config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        bob_id = resp.json()["creature_id"]
        assert resp.json()["status"] == "running"

        # Hot-plug into a session that does not exist → 400.
        resp = client.post(
            "/api/sessions/active/no-such-session/creatures",
            json={"name": "ghost", "config_path": str(creature_dir)},
        )
        assert resp.status_code == 400

        # The session now reports two creatures.
        resp = client.get(f"/api/sessions/active/{session_id}/creatures")
        assert resp.status_code == 200
        names = {c["name"] for c in resp.json()}
        assert names == {"alice", "bob"}
        # Listing creatures of an unknown session → 404.
        resp = client.get("/api/sessions/active/no-such-session/creatures")
        assert resp.status_code == 404

        # Two creatures → the session now shows up in the legacy
        # terrarium list and the legacy terrarium accessor resolves it.
        resp = client.get("/api/sessions/active/terrariums")
        assert resp.status_code == 200
        assert [t["terrarium_id"] for t in resp.json()] == [session_id]
        resp = client.get(f"/api/sessions/active/terrariums/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["terrarium_id"] == session_id
        assert set(resp.json()["creatures"]) == {"alice", "bob"}
        # And it is NOT in the legacy solo-agent list anymore.
        resp = client.get("/api/sessions/active/agents")
        assert resp.status_code == 200
        assert resp.json() == []

        # Declare a shared channel and send a message to it.
        resp = client.post(
            f"/api/sessions/topology/{session_id}/channels",
            json={"name": "team", "description": "shared chat"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

        # Re-declaring the same channel name → 400 (documented).
        resp = client.post(
            f"/api/sessions/topology/{session_id}/channels",
            json={"name": "team", "description": "dup"},
        )
        assert resp.status_code == 400

        # List channels — the new one is present.
        resp = client.get(f"/api/sessions/topology/{session_id}/channels")
        assert resp.status_code == 200
        assert any(c["name"] == "team" for c in resp.json())

        resp = client.post(
            f"/api/sessions/topology/{session_id}/channels/team/send",
            json={"content": "hello team", "sender": "human"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"

        # Sending to a channel that does not exist → 400.
        resp = client.post(
            f"/api/sessions/topology/{session_id}/channels/ghost/send",
            json={"content": "x", "sender": "human"},
        )
        assert resp.status_code == 400

        # The channel + its message are observable on the channel read.
        resp = client.get(f"/api/sessions/topology/{session_id}/channels/team")
        assert resp.status_code == 200
        channel_info = resp.json()
        assert channel_info["name"] == "team"
        # Reading an unknown channel → 404.
        resp = client.get(f"/api/sessions/topology/{session_id}/channels/ghost")
        assert resp.status_code == 404

        # Wire alice as a listener + bob as a sender on the channel.
        resp = client.post(
            f"/api/sessions/topology/{session_id}/creatures/{alice_id}/wire",
            json={"channel": "team", "direction": "listen"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "wired"}
        resp = client.post(
            f"/api/sessions/topology/{session_id}/creatures/{bob_id}/wire",
            json={"channel": "team", "direction": "send"},
        )
        assert resp.status_code == 200
        # Wiring onto a non-existent channel → 400.
        resp = client.post(
            f"/api/sessions/topology/{session_id}/creatures/{alice_id}/wire",
            json={"channel": "ghost", "direction": "listen"},
        )
        assert resp.status_code == 400
        # Unwire alice again.
        resp = client.request(
            "DELETE",
            f"/api/sessions/topology/{session_id}/creatures/{alice_id}/wire",
            json={"channel": "team", "direction": "listen"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "unwired"}

        # Merging a session with itself is a no-op (merged == False).
        resp = client.post(f"/api/sessions/topology/{session_id}/merge/{session_id}")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": session_id, "merged": False}
        # Merging with an unknown session → 404.
        resp = client.post(f"/api/sessions/topology/{session_id}/merge/no-such-session")
        assert resp.status_code == 404

        # connect referencing a creature that does not exist →
        # documented 400 (KeyError/ValueError in topology_lib).
        resp = client.post(
            f"/api/sessions/topology/{session_id}/connect",
            json={"sender": alice_id, "receiver": "ghost-creature"},
        )
        assert resp.status_code == 400
        resp = client.post(
            f"/api/sessions/topology/{session_id}/disconnect",
            json={"sender": alice_id, "receiver": "ghost-creature"},
        )
        assert resp.status_code == 400
        # Channel-info read on an unknown session → 404; channel list
        # on an unknown session → 404.
        resp = client.get(f"/api/sessions/topology/no-such-session/channels/team")
        assert resp.status_code == 404
        resp = client.get("/api/sessions/topology/no-such-session/channels")
        assert resp.status_code == 404
        # Wiring a creature on an unknown channel through the per-
        # creature unwire verb → 400.
        resp = client.request(
            "DELETE",
            f"/api/sessions/topology/{session_id}/creatures/{alice_id}/wire",
            json={"channel": "ghost-channel", "direction": "listen"},
        )
        assert resp.status_code == 400

        # ── Output-wiring CRUD (frontend: wiringAPI) ──────────────────
        wbase = f"/api/sessions/wiring/{session_id}/creatures/{alice_id}"
        # Initially no direct output edges.
        resp = client.get(f"{wbase}/outputs")
        assert resp.status_code == 200
        assert resp.json() == {"outputs": []}
        # Wire alice → bob as a direct output edge.
        resp = client.post(f"{wbase}/outputs", json={"to": "bob", "with_content": True})
        assert resp.status_code == 200
        assert resp.json()["status"] == "wired"
        edge_id = resp.json()["edge_id"]
        assert edge_id
        # The edge is now listed.
        resp = client.get(f"{wbase}/outputs")
        assert resp.status_code == 200
        outputs = resp.json()["outputs"]
        assert any(e.get("to") == "bob" for e in outputs)
        # sinks endpoint reports the empty secondary-sink list.
        resp = client.get(f"{wbase}/sinks")
        assert resp.status_code == 200
        assert resp.json() == {"sinks": []}
        # sinks / unwire-sink on an unknown creature → 404; detaching a
        # sink that was never wired → "not_found" (idempotent, 200).
        resp = client.get(f"/api/sessions/wiring/{session_id}/creatures/ghost/sinks")
        assert resp.status_code == 404
        resp = client.request("DELETE", f"{wbase}/sinks/no-such-sink")
        assert resp.status_code == 200
        assert resp.json() == {"status": "not_found"}
        resp = client.request(
            "DELETE",
            f"/api/sessions/wiring/{session_id}/creatures/ghost/sinks/x",
        )
        assert resp.status_code == 404
        # Wiring an output edge on an unknown creature → 404.
        resp = client.post(
            f"/api/sessions/wiring/{session_id}/creatures/ghost/outputs",
            json={"to": "bob"},
        )
        assert resp.status_code == 404
        resp = client.request(
            "DELETE",
            f"/api/sessions/wiring/{session_id}/creatures/ghost/outputs/e",
        )
        assert resp.status_code == 404

        # Runtime-graph snapshot WHILE the alice→bob output edge is
        # still wired — the snapshot's ``output_edges`` carries it,
        # resolved to bob's creature id (exercises the snapshot's
        # output-edge + target-resolution path).
        resp = client.get("/api/runtime/graph")
        assert resp.status_code == 200
        wired_graph = next(
            g for g in resp.json()["graphs"] if g["graph_id"] == session_id
        )
        edge = next(e for e in wired_graph["output_edges"] if e.get("to") == "bob")
        assert edge["from"] == alice_id
        assert edge["to_creature_id"] == bob_id
        assert edge["graph_id"] == session_id

        # Detach the edge.
        resp = client.request("DELETE", f"{wbase}/outputs/{edge_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "unwired"}
        # Detaching it again → "not_found" (idempotent, still 200).
        resp = client.request("DELETE", f"{wbase}/outputs/{edge_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "not_found"}
        # Output-wiring ops on an unknown creature → 404.
        resp = client.get(f"/api/sessions/wiring/{session_id}/creatures/ghost/outputs")
        assert resp.status_code == 404

        # Runtime-graph snapshot over HTTP (frontend: runtimeGraphAPI).
        resp = client.get("/api/runtime/graph")
        assert resp.status_code == 200
        snapshot = resp.json()
        graphs = {g["graph_id"]: g for g in snapshot["graphs"]}
        assert session_id in graphs
        graph = graphs[session_id]
        graph_creatures = {c["name"] for c in graph["creatures"]}
        assert graph_creatures == {"alice", "bob"}
        graph_channels = {c["name"] for c in graph["channels"]}
        assert "team" in graph_channels

        # Metrics now reports a multi-creature graph as a terrarium.
        resp = client.get("/api/metrics/snapshot")
        assert resp.status_code == 200
        gauges = resp.json()["gauges"]
        assert gauges["terrariums_running"] == 1
        assert gauges["agents_running"] == 2

        # Runtime-graph WebSocket: a fresh client gets a ``subscribed``
        # frame then a ``snapshot`` frame carrying the same topology;
        # then a live channel send is streamed to it as a flat
        # ``channel_message`` event (exercises the WS channel-observer
        # callback + threadsafe enqueue path).
        with client.websocket_connect("/ws/runtime/graph") as ws:
            subscribed = ws.receive_json()
            assert subscribed["type"] == "subscribed"
            snap_frame = ws.receive_json()
            assert snap_frame["type"] == "snapshot"
            ws_graphs = {g["graph_id"]: g for g in snap_frame["snapshot"]["graphs"]}
            assert session_id in ws_graphs
            assert {c["name"] for c in ws_graphs[session_id]["creatures"]} == {
                "alice",
                "bob",
            }
            # Drive a channel send and read frames until the flat
            # ``channel_message`` event for it arrives (topology events
            # from earlier mutations may be queued ahead of it).
            resp = client.post(
                f"/api/sessions/topology/{session_id}/channels/team/send",
                json={"content": "graph ws message", "sender": "human"},
            )
            assert resp.status_code == 200
            for _ in range(20):
                frame = ws.receive_json()
                if frame["type"] == "channel_message":
                    assert frame["channel"] == "team"
                    assert frame["content"] == "graph ws message"
                    assert frame["graph_id"] == session_id
                    break
            else:
                raise AssertionError("no channel_message frame on /ws/runtime/graph")

        # Channel-observer WS: subscribing to a real session opens the
        # stream; a bogus session id gets an explicit error frame.
        with client.websocket_connect(f"/ws/sessions/{session_id}/observer") as ws:
            # Send one message after the subscription is live so the
            # observer has a frame to deliver.
            resp = client.post(
                f"/api/sessions/topology/{session_id}/channels/team/send",
                json={"content": "observed message", "sender": "human"},
            )
            assert resp.status_code == 200
            frame = ws.receive_json()
            assert frame["type"] == "channel_message"
            assert frame["channel"] == "team"
            assert frame["content"] == "observed message"
        with client.websocket_connect("/ws/sessions/no-such-session/observer") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert "not found" in frame["content"]

        # Log-tail WS: the server either streams a ``meta`` frame (a log
        # file exists for this process) or an ``error`` frame (none
        # found). Both are valid first frames — assert on the type.
        with client.websocket_connect("/ws/logs") as ws:
            frame = ws.receive_json()
            assert frame["type"] in {"meta", "error"}

        # File-watch WS: a live creature with a working dir streams a
        # ``ready`` frame; an unknown agent gets an ``error`` frame.
        with client.websocket_connect(f"/ws/files/{alice_id}") as ws:
            frame = ws.receive_json()
            assert frame["type"] in {"ready", "error"}
        with client.websocket_connect("/ws/files/ghost-agent") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert "not found" in frame["text"]

        # Remove the hot-plugged creature; the session shrinks back.
        resp = client.delete(f"/api/sessions/active/{session_id}/creatures/{bob_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "removed"}
        resp = client.get(f"/api/sessions/active/{session_id}/creatures")
        assert resp.status_code == 200
        assert {c["name"] for c in resp.json()} == {"alice"}
        # Removing it again → 404.
        resp = client.delete(f"/api/sessions/active/{session_id}/creatures/{bob_id}")
        assert resp.status_code == 404

        client.delete(f"/api/sessions/active/{session_id}")

        # ── connect / disconnect (graph editor's wire-by-id path) ─────
        # Driven on a fresh, isolated session: ``disconnect`` removing
        # the only link between two creatures auto-splits the graph, so
        # keeping it self-contained avoids perturbing the workflow
        # above. Spin up a two-creature session, connect them over a
        # channel, then disconnect.
        resp = client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        cd_sid = resp.json()["session_id"]
        cd_alice = resp.json()["agent_id"]
        resp = client.post(
            f"/api/sessions/active/{cd_sid}/creatures",
            json={"name": "carol", "config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        cd_carol = resp.json()["creature_id"]
        # ``connect`` wires the pair over a fresh broadcast channel.
        resp = client.post(
            f"/api/sessions/topology/{cd_sid}/connect",
            json={"sender": cd_alice, "receiver": cd_carol, "channel": "link1"},
        )
        assert resp.status_code == 200
        assert resp.json()["channel"] == "link1"
        # The new channel is visible on the topology channel list.
        resp = client.get(f"/api/sessions/topology/{cd_sid}/channels")
        assert resp.status_code == 200
        assert any(c["name"] == "link1" for c in resp.json())
        # ``disconnect`` drops the link again — the result names the
        # channel that was removed.
        resp = client.post(
            f"/api/sessions/topology/{cd_sid}/disconnect",
            json={"sender": cd_alice, "receiver": cd_carol, "channel": "link1"},
        )
        assert resp.status_code == 200
        assert "link1" in resp.json()["channels"]
        # Tear the isolated session(s) down. The disconnect may have
        # auto-split the graph; stop every session carol/alice landed
        # in so the suite leaves no live sessions behind.
        for s in client.get("/api/sessions/active").json():
            client.delete(f"/api/sessions/active/{s['session_id']}")
        assert client.get("/api/sessions/active").json() == []


def test_session_dir_env_isolation(client: TestClient, tmp_path: Path) -> None:
    """Sanity guard: the fixture really redirected ``KT_SESSION_DIR``
    into ``tmp_path`` so the suite never touches the user's real
    session directory."""
    assert os.environ["KT_SESSION_DIR"].startswith(str(tmp_path))
    assert lifecycle._session_dir() == os.environ["KT_SESSION_DIR"]


# ── multi-node (lab-host) api workflow — regression coverage ──────────
#
# The integration tier had ZERO multi-node coverage. These drive the
# real ``api/`` consumers (the FastAPI routes + WS handlers) against a
# ``MultiNodeTerrariumService``-shaped service — the same surface the
# e2e tier exercises against a real HostEngine, narrowed to the api
# folder. They reproduce two reported lab-host failures: the
# ``attach/policies/<session_id>`` 404 and the chat-WS-by-name
# "creature not found" close.


class _IntegMultiNodeService:
    """A ``MultiNodeTerrariumService``-shaped fake for the api tier.

    Just enough Protocol surface for the routes under test: ``_home``
    + ``connected_nodes`` mark it multi-node, ``get_creature_info`` is
    id-only (the real Protocol contract), ``coordination_engine`` is a
    bare agent-free :class:`Terrarium` the app lifespan attaches its
    runtime prompt to.
    """

    def __init__(self, coordination_engine, infos, *, homes=None):
        self.coordination_engine = coordination_engine
        self._by_id = {i.creature_id: i for i in infos}
        # creature_id → worker node id (defaults all to worker-1).
        self._home = {
            i.creature_id: (homes or {}).get(i.creature_id, "worker-1") for i in infos
        }
        self.host = "HOST"
        self.demux = "DEMUX"
        self.connect_calls: list[tuple[str, str]] = []

    def connected_nodes(self):
        return tuple(sorted(set(self._home.values())))

    async def list_creatures(self):
        return tuple(self._by_id.values())

    async def get_creature_info(self, cid):
        return self._by_id.get(cid)  # id-only — names do not resolve

    async def get_graph(self, graph_id):
        from types import SimpleNamespace

        members = {
            i.creature_id for i in self._by_id.values() if i.graph_id == graph_id
        }
        if not members:
            return None
        return SimpleNamespace(graph_id=graph_id, creature_ids=members)

    async def connect(self, sender_id, receiver_id, *, channel=None):
        from kohakuterrarium.terrarium.events import ConnectionResult

        # Capture ``channel`` so route tests can pin that the merge
        # endpoint passes the user-picked name through (vs. defaulting
        # to None which makes the underlying connect auto-name a fresh
        # ``a_to_b`` channel — the wrong UX).
        self.connect_calls.append((sender_id, receiver_id, channel))
        sender = self._by_id.get(sender_id)
        return ConnectionResult(
            channel=channel or "bridge",
            graph_id=sender.graph_id if sender else "",
            delta_kind="cross_node",
        )

    async def _resolve_home(self, cid):
        return self._home.get(cid)

    async def attach_policies(self, creature_id):
        if creature_id not in self._by_id:
            raise KeyError(creature_id)  # not a creature id
        return ["log", "trace", "io"]

    async def session_attach_policies(self, session_id):
        # Any known graph id is accepted — the worker session is live.
        if session_id in {i.graph_id for i in self._by_id.values()}:
            return ["log", "observer", "trace"]
        raise KeyError(session_id)


class TestMultiNodeApiIntegration:
    """The api folder driven against a lab-host service shape."""

    def _client(self, monkeypatch, tmp_path) -> tuple[TestClient, str, str]:
        from kohakuterrarium.terrarium.service import CreatureInfo

        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
        coord = Terrarium(session_dir=str(session_dir))
        info = CreatureInfo(
            creature_id="quiet_meadow_abcd1234",
            name="quiet-meadow",
            graph_id="graph_deadbeef",
            is_running=True,
            is_privileged=True,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        service = _IntegMultiNodeService(coord, [info])
        set_service(service)
        app = create_app()
        client = TestClient(app)
        client.__enter__()
        return client, info.creature_id, info.graph_id

    def test_attach_policies_for_worker_session_id(self, monkeypatch, tmp_path):
        # The frontend hits ``/api/attach/policies/<session_id>`` — in
        # lab-host mode the host engine has neither the creature nor the
        # graph, so the route 404s. It must resolve a known worker graph.
        client, _cid, gid = self._client(monkeypatch, tmp_path)
        try:
            resp = client.get(f"/api/attach/policies/{gid}")
            assert resp.status_code == 200, (
                f"/api/attach/policies/{gid} returned {resp.status_code} "
                f"for a live worker session: {resp.text}"
            )
            assert "log" in resp.json()["policies"]
        finally:
            client.__exit__(None, None, None)
            set_service(None)

    def test_chat_ws_attach_by_creature_name(self, monkeypatch, tmp_path):
        # The frontend keys its chat tab off the creature's display
        # name (``/creatures/quiet-meadow/chat``). In lab-host mode the
        # WS handler must resolve the name to the worker creature, not
        # close the socket with "creature not found".
        from kohakuterrarium.studio.attach import io as io_mod

        dispatched: list[str] = []

        async def _fake_remote(websocket, service, creature_info, session_id):
            dispatched.append(creature_info.creature_id)
            await websocket.send_json({"type": "idle", "source": "test"})

        monkeypatch.setattr(io_mod, "_attach_io_remote", _fake_remote)
        client, cid, gid = self._client(monkeypatch, tmp_path)
        try:
            # Attach BY NAME — the frontend's chat-tab key.
            url = f"/ws/sessions/{gid}/creatures/quiet-meadow/chat"
            with client.websocket_connect(url) as ws:
                frame = ws.receive_json()
            assert frame.get("type") != "error", (
                f"chat WS attach by name produced an error frame: {frame} "
                "— the WebSocket closes 'creature not found'"
            )
            assert dispatched == [cid], (
                "attach_io did not resolve the creature display name to "
                f"the worker creature {cid!r}: dispatched={dispatched}"
            )
        finally:
            client.__exit__(None, None, None)
            set_service(None)

    def test_cross_node_merge_route(self, monkeypatch, tmp_path):
        # Regression: the graph editor's cross-molecule wire posts
        # ``/api/sessions/topology/{a}/merge/{b}``. With creatures on
        # two different workers the route must resolve both graphs
        # through the service and bridge via ``service.connect`` — the
        # pre-fix route walked the empty host coordination engine and
        # returned 404 for every cross-node merge.
        from kohakuterrarium.terrarium.service import CreatureInfo

        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
        coord = Terrarium(session_dir=str(session_dir))
        a = CreatureInfo(
            creature_id="alice_aaaa1111",
            name="alice",
            graph_id="graph_w1",
            is_running=True,
            is_privileged=True,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        b = CreatureInfo(
            creature_id="bob_bbbb2222",
            name="bob",
            graph_id="graph_w2",
            is_running=True,
            is_privileged=True,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        service = _IntegMultiNodeService(
            coord,
            [a, b],
            homes={"alice_aaaa1111": "worker-1", "bob_bbbb2222": "worker-2"},
        )
        set_service(service)
        app = create_app()
        client = TestClient(app)
        client.__enter__()
        try:
            resp = client.post("/api/sessions/topology/graph_w1/merge/graph_w2")
            assert resp.status_code == 200, (
                f"cross-node merge returned {resp.status_code} — the route "
                f"walked the empty host engine instead of the service: "
                f"{resp.text}"
            )
            assert resp.json()["merged"] is True
            # The bridge went through ``service.connect`` — the only
            # cross-node-capable path.  Channel is None because the
            # frontend didn't pass one in this call.
            assert service.connect_calls == [("alice_aaaa1111", "bob_bbbb2222", None)]
        finally:
            client.__exit__(None, None, None)
            set_service(None)

    def test_merge_route_threads_channel_param_to_connect(self, monkeypatch, tmp_path):
        """``POST /merge/{b}?channel=foo`` must pass ``channel=foo`` to
        ``service.connect`` so the merge reuses the existing user-
        picked channel name instead of creating a fresh auto-named
        ``{a}_to_{b}`` bridge.  Without this the graph editor's
        cross-molecule wire spawns a parallel channel alongside the
        user's, breaking the "user channel name is meaningful" UX
        invariant.
        """
        from kohakuterrarium.terrarium.service import CreatureInfo

        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
        coord = Terrarium(session_dir=str(session_dir))
        a = CreatureInfo(
            creature_id="aa",
            name="alpha",
            graph_id="graph_w1",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        b = CreatureInfo(
            creature_id="bb",
            name="bravo",
            graph_id="graph_w2",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        service = _IntegMultiNodeService(
            coord, [a, b], homes={"aa": "worker-1", "bb": "worker-2"}
        )
        set_service(service)
        app = create_app()
        client = TestClient(app)
        client.__enter__()
        try:
            resp = client.post(
                "/api/sessions/topology/graph_w1/merge/graph_w2?channel=my_named_ch"
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["merged"] is True
            # The user-picked channel threaded through to ``connect``.
            assert service.connect_calls == [("aa", "bb", "my_named_ch")]
        finally:
            client.__exit__(None, None, None)
            set_service(None)

    def test_cross_node_wire_route_replicates_user_channel(self, monkeypatch, tmp_path):
        """The user-named-channel cross-node wire bug ("VERY BAD").

        ``POST /api/sessions/topology/{graph_b}/creatures/{b_id}/wire``
        with a channel that lives on worker-1's graph (not worker-2's)
        must route through the service Protocol AND replicate the
        channel on the target.  Pre-fix the route forwarded straight to
        the target worker, which 400'd because the channel wasn't
        there, and the graph editor fell back to an auto-named
        ``a_to_b`` channel that ignored the user's name.

        This integration test stubs the service Protocol to record what
        the route did — full e2e proof lives in
        ``tests/e2e/test_multinode_real.py``.
        """
        from kohakuterrarium.terrarium.service import CreatureInfo

        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))
        coord = Terrarium(session_dir=str(session_dir))
        a = CreatureInfo(
            creature_id="alpha_aaaa",
            name="alpha",
            graph_id="graph_w1",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        b = CreatureInfo(
            creature_id="bravo_bbbb",
            name="bravo",
            graph_id="graph_w2",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        wire_calls: list[tuple] = []

        class _Service(_IntegMultiNodeService):
            async def wire_creature(
                self, session_id, creature_id, channel, direction, *, enabled=True
            ):
                wire_calls.append(
                    (session_id, creature_id, channel, direction, enabled)
                )

        service = _Service(
            coord,
            [a, b],
            homes={"alpha_aaaa": "worker-1", "bravo_bbbb": "worker-2"},
        )
        set_service(service)
        app = create_app()
        client = TestClient(app)
        client.__enter__()
        try:
            resp = client.post(
                f"/api/sessions/topology/graph_w2/creatures/{b.creature_id}/wire",
                json={"channel": "my_channel", "direction": "listen"},
            )
            assert resp.status_code == 200, (
                f"cross-node wire route returned {resp.status_code} — the "
                f"route should delegate to the service so multi-node lazy "
                f"replication can engage: {resp.text}"
            )
            assert wire_calls == [
                ("graph_w2", b.creature_id, "my_channel", "listen", True)
            ], (
                "wire_creature did not reach the service Protocol — the "
                "route bypassed it and short-circuited the cross-node "
                "replication path"
            )
        finally:
            client.__exit__(None, None, None)
            set_service(None)
