"""E2E journey — the HTTP+WS Studio management surface.

This is a *whole-project* journey test: a single real
:func:`create_app` FastAPI app driven through
``fastapi.testclient.TestClient`` exactly the way the Vue frontend's
``src/kohakuterrarium-frontend/src/utils/api.js`` drives the Studio
management routes. It answers one question: *is the Studio surface —
catalog discovery, workspace authoring, session start, persistence
viewer, fork, resume, attach hints — runnable end to end over HTTP?*

The only seam is the LLM: BOTH ``create_llm_provider`` bind points
(``bootstrap.llm`` and ``bootstrap.agent_init``) are monkeypatched to
a deterministic :class:`ScriptedLLM`. Everything else runs for real —
the engine, a real :class:`LocalTerrariumService`, real on-disk
``.kohakutr`` session files, real workspace directories on disk, the
real catalog / editor / persistence routers.

Two journey methods, each a complete multi-step workflow:

* :meth:`test_authoring_to_session_journey` — browse the builtin
  catalog, open a workspace, scaffold a creature, edit its config +
  a module + its prompt, validate it, save it, start a live session
  from it, take a chat turn, read history back.
* :meth:`test_persistence_viewer_and_attach_journey` — run a creature
  turn so a ``.kohakutr`` lands on disk, list saved sessions, open
  every viewer pane (tree / summary / turns / events), fork the
  session, diff parent vs. child, resume it into the engine, read
  the attach policy hints.
"""

import os
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

# Deterministic assistant replies — the ScriptedLLM hands out the next
# unused entry per LLM call; one plain creature turn = one call.
_REPLY_ONE = "Scripted studio reply one."
_REPLY_TWO = "Scripted studio reply two."


# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def scripted_llm(monkeypatch: pytest.MonkeyPatch) -> ScriptedLLM:
    """Replace the live LLM provider at BOTH bind points.

    ``bootstrap.llm.create_llm_provider`` is the canonical factory;
    ``bootstrap.agent_init`` imports it by name, so without the second
    patch the agent-init path would still reach a real provider.
    """
    llm = ScriptedLLM([_REPLY_ONE, _REPLY_TWO, _REPLY_TWO, _REPLY_TWO])

    def _fake_create(config, llm_override=None):
        return llm

    monkeypatch.setattr(_bootstrap_llm, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init, "create_llm_provider", _fake_create)
    return llm


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """A real empty on-disk workspace directory the studio can open."""
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scripted_llm: ScriptedLLM,
) -> Iterator[TestClient]:
    """A TestClient over a real ``create_app()`` with a real service.

    ``KT_SESSION_DIR`` is redirected at ``tmp_path`` so persistence
    (saved-session list / resume / viewer) reads and writes the same
    isolated directory the engine saves into. The catalog workspace
    module-global is reset on teardown so journeys don't leak the
    open workspace into the next test.
    """
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setenv("KT_SESSION_DIR", str(session_dir))

    engine = Terrarium(session_dir=str(session_dir))
    service = LocalTerrariumService(engine)
    set_service(service)

    app = create_app()
    # ``with TestClient`` runs the lifespan — startup attaches the
    # runtime-graph prompt, shutdown drives ``engine.shutdown()``.
    with TestClient(app) as test_client:
        yield test_client

    set_service(None)
    catalog_deps.set_workspace(None)


# ── helpers ───────────────────────────────────────────────────────────


def _scaffold_workspace_creature(
    client: TestClient, workspace_root: Path, name: str
) -> dict:
    """Open the workspace and scaffold + edit + validate + save a
    creature exactly as the studio creature editor does.

    Returns the loaded creature payload after the final save.
    """
    # Open the workspace (frontend: studio workspace/open).
    resp = client.post("/api/studio/workspace/open", json={"path": str(workspace_root)})
    assert resp.status_code == 200
    assert resp.json()["root"] == str(workspace_root.resolve())

    # Scaffold a fresh creature directory (POST /studio/creatures).
    resp = client.post("/api/studio/creatures", json={"name": name})
    assert resp.status_code == 201
    scaffolded = resp.json()
    assert scaffolded["name"] == name
    # The scaffold seeds a config + a prompts/system.md sidecar.
    assert scaffolded["config"]["name"] == name
    assert "prompts/system.md" in scaffolded["prompts"]

    # Edit the config + prompt and save it back (PUT /studio/creatures).
    edited_config = dict(scaffolded["config"])
    edited_config["description"] = "an e2e-authored studio creature"
    new_prompt = f"# {name}\nYou are the {name} e2e creature. Reply tersely."
    resp = client.put(
        f"/api/studio/creatures/{name}",
        json={
            "config": edited_config,
            "prompts": {"prompts/system.md": new_prompt},
        },
    )
    assert resp.status_code == 200
    saved = resp.json()
    assert saved["config"]["description"] == "an e2e-authored studio creature"
    assert saved["prompts"]["prompts/system.md"] == new_prompt
    return saved


class TestApiStudioJourney:
    """Fat end-to-end journeys over the HTTP Studio management surface."""

    def test_authoring_to_session_journey(
        self,
        client: TestClient,
        workspace_root: Path,
        scripted_llm: ScriptedLLM,
    ) -> None:
        """Catalog browse → workspace authoring → validate → start a
        session → chat → history.

        Mirrors the studio frontend's authoring flow: browse the
        builtin tool / sub-agent / model / template catalog, open a
        workspace, scaffold + edit + validate + save a creature, then
        start a live session from that creature directory and take a
        turn.
        """
        # 1. Catalog: browse builtin tools — the read-only module pool.
        resp = client.get("/api/studio/catalog/tools")
        assert resp.status_code == 200
        tool_names = {t["name"] for t in resp.json()}
        # ``read`` / ``write`` / ``bash`` are canonical builtin tools.
        assert {"read", "write", "bash"} <= tool_names

        # The per-tool doc route returns the full skill documentation.
        resp = client.get("/api/studio/catalog/tools/bash/doc")
        assert resp.status_code == 200
        assert resp.json()["name"] == "bash"

        # Builtin sub-agents — the ``response`` output sub-agent ships.
        resp = client.get("/api/studio/catalog/subagents")
        assert resp.status_code == 200
        subagent_names = {s["name"] for s in resp.json()}
        assert "response" in subagent_names

        # Triggers catalog — the universal setup-tool triggers always
        # ship; ``add_timer`` is one of them.
        resp = client.get("/api/studio/catalog/triggers")
        assert resp.status_code == 200
        assert "add_timer" in {t["name"] for t in resp.json()}
        # Plugins / inputs / outputs catalogs surface the installed
        # ``kt-biome`` package's modules (workspace + package scope).
        resp = client.get("/api/studio/catalog/plugins")
        assert resp.status_code == 200
        assert "checkpoint" in {p["name"] for p in resp.json()}
        resp = client.get("/api/studio/catalog/inputs")
        assert resp.status_code == 200
        assert "discord_input" in {i["name"] for i in resp.json()}
        resp = client.get("/api/studio/catalog/outputs")
        assert resp.status_code == 200
        assert "discord_output" in {o["name"] for o in resp.json()}

        # Models — the LLM profile table is non-empty and entries carry
        # a usable selector ``name``.
        resp = client.get("/api/studio/catalog/models")
        assert resp.status_code == 200
        models = resp.json()
        assert models and all("name" in m for m in models)

        # Embedding presets + plugin-hook catalog round out the catalog.
        resp = client.get("/api/studio/catalog/embedding_presets")
        assert resp.status_code == 200
        resp = client.get("/api/studio/catalog/plugin_hooks")
        assert resp.status_code == 200
        assert resp.json()

        # Templates — the scaffolding template list the editor offers.
        resp = client.get("/api/studio/templates")
        assert resp.status_code == 200
        template_ids = {t["id"] for t in resp.json()}
        assert "creature-minimal" in template_ids

        # 2. Editors: scaffold + edit + save a workspace creature.
        _scaffold_workspace_creature(client, workspace_root, "scout")

        # The workspace summary now lists exactly that creature.
        resp = client.get("/api/studio/workspace")
        assert resp.status_code == 200
        summary = resp.json()
        assert [c["name"] for c in summary["creatures"]] == ["scout"]

        # 3. Editors: author a workspace module of EVERY kind — each kind
        #    routes through its own codegen module (tool / subagent /
        #    plugin / trigger / io). Scaffold, list, load it back, then
        #    drive a raw-mode + a simple-mode save through the same
        #    /studio/modules route the frontend module editor uses.
        resp = client.post("/api/studio/modules/tools", json={"name": "echo_tool"})
        assert resp.status_code == 201
        resp = client.get("/api/studio/modules/tools")
        assert resp.status_code == 200
        assert [m["name"] for m in resp.json()] == ["echo_tool"]

        for kind in ("subagents", "plugins", "triggers", "inputs", "outputs"):
            mod_name = f"{kind[:-1]}_one"
            resp = client.post(f"/api/studio/modules/{kind}", json={"name": mod_name})
            assert resp.status_code == 201, kind
            scaffolded_mod = resp.json()
            assert scaffolded_mod["name"] == mod_name
            # The kind listing surfaces exactly the scaffolded module.
            resp = client.get(f"/api/studio/modules/{kind}")
            assert resp.status_code == 200
            assert [m["name"] for m in resp.json()] == [mod_name]
            # load-back parses the source through the kind's codegen.
            resp = client.get(f"/api/studio/modules/{kind}/{mod_name}")
            assert resp.status_code == 200
            loaded_mod = resp.json()
            assert loaded_mod["name"] == mod_name
            assert loaded_mod["kind"] == kind
            # A raw-mode save round-trips the exact bytes back to disk.
            raw_src = loaded_mod["raw_source"] + "\n# edited-by-journey\n"
            resp = client.put(
                f"/api/studio/modules/{kind}/{mod_name}",
                json={"mode": "raw", "raw_source": raw_src},
            )
            assert resp.status_code == 200, kind
            resp = client.get(f"/api/studio/modules/{kind}/{mod_name}")
            assert "# edited-by-journey" in resp.json()["raw_source"]
            # A simple-mode save with no raw body re-renders or patches
            # in place through the codegen update path.
            resp = client.put(
                f"/api/studio/modules/{kind}/{mod_name}",
                json={"mode": "simple", "form": {}, "execute_body": ""},
            )
            assert resp.status_code == 200, kind

        # A raw-mode save with an empty body is a hard 400 (invalid input).
        resp = client.put(
            "/api/studio/modules/tools/echo_tool",
            json={"mode": "raw", "raw_source": ""},
        )
        assert resp.status_code == 400
        # An unknown module kind is a hard 400 on every modules verb.
        resp = client.get("/api/studio/modules/nonsense-kind")
        assert resp.status_code == 400
        resp = client.post("/api/studio/modules/nonsense-kind", json={"name": "x"})
        assert resp.status_code == 400
        # Scaffolding a duplicate name is a hard 409.
        resp = client.post("/api/studio/modules/tools", json={"name": "echo_tool"})
        assert resp.status_code == 409
        # Loading a module that does not exist is a hard 404.
        resp = client.get("/api/studio/modules/tools/no-such-tool")
        assert resp.status_code == 404

        # 3b. module_schema route — the editor's option-form source. The
        #     builtin schemas are pure introspection; a custom entry
        #     AST-parses the workspace module we just authored.
        resp = client.post(
            "/api/studio/module_schema", json={"kind": "subagents", "type": "builtin"}
        )
        assert resp.status_code == 200
        sub_params = {p["name"] for p in resp.json()["params"]}
        assert {"max_turns", "interactive"} <= sub_params
        resp = client.post(
            "/api/studio/module_schema", json={"kind": "plugins", "type": "builtin"}
        )
        assert resp.status_code == 200
        assert "priority" in {p["name"] for p in resp.json()["params"]}
        # A custom-type schema request resolves the workspace module
        # source and AST-parses its target class __init__.
        resp = client.post(
            "/api/studio/module_schema",
            json={
                "kind": "tools",
                "type": "custom",
                "module": "modules/tools/echo_tool.py",
            },
        )
        assert resp.status_code == 200
        custom_schema_result = resp.json()
        assert "params" in custom_schema_result
        # A custom entry with no module path returns a missing_module
        # warning rather than a hard error.
        resp = client.post(
            "/api/studio/module_schema", json={"kind": "tools", "type": "custom"}
        )
        assert resp.status_code == 200
        assert [w["code"] for w in resp.json()["warnings"]] == ["missing_module"]

        # 3c. validate/module — a syntax check over module source.
        resp = client.post(
            "/api/studio/validate/module",
            json={"kind": "tools", "source": "x = 1\n"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "errors": []}
        resp = client.post(
            "/api/studio/validate/module",
            json={"kind": "tools", "source": "def broken(\n"},
        )
        assert resp.status_code == 200
        bad_mod = resp.json()
        assert bad_mod["ok"] is False
        assert bad_mod["errors"][0]["code"] == "syntax_error"

        # 3d. manifest sync — append the module into kohaku.yaml so the
        #     catalog can discover it; the second call is idempotent.
        resp = client.post(
            "/api/studio/workspace/manifest/sync",
            json={"kind": "tools", "name": "echo_tool"},
        )
        assert resp.status_code == 200
        resp = client.post(
            "/api/studio/workspace/manifest/sync",
            json={"kind": "tools", "name": "echo_tool"},
        )
        assert resp.status_code == 200
        # Syncing a non-existent module is a hard 404.
        resp = client.post(
            "/api/studio/workspace/manifest/sync",
            json={"kind": "tools", "name": "ghost-tool"},
        )
        assert resp.status_code == 404

        # 3e. module skill-doc sidecar — write + read it back.
        resp = client.put(
            "/api/studio/modules/tools/echo_tool/doc",
            json={"content": "# echo_tool\nProcedural notes."},
        )
        assert resp.status_code == 200
        resp = client.get("/api/studio/modules/tools/echo_tool/doc")
        assert resp.status_code == 200
        assert resp.json()["content"] == "# echo_tool\nProcedural notes."

        # 4. Editors: edit a prompt file directly via the prompt route.
        resp = client.put(
            "/api/studio/creatures/scout/prompts/prompts/system.md",
            json={"content": "# scout\nYou are scout. Be brief."},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "path": "prompts/system.md"}
        resp = client.get("/api/studio/creatures/scout/prompts/prompts/system.md")
        assert resp.status_code == 200
        assert resp.json()["content"] == "# scout\nYou are scout. Be brief."

        # 5. Validate the creature config — a valid config validates clean.
        resp = client.get("/api/studio/creatures/scout")
        assert resp.status_code == 200
        creature_config = resp.json()["config"]
        resp = client.post(
            "/api/studio/validate/creature", json={"config": creature_config}
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "errors": []}

        # A config referencing a non-existent builtin tool fails
        # validation with the exact ``unknown_builtin_tool`` code.
        bad_config = dict(creature_config)
        bad_config["tools"] = [{"type": "builtin", "name": "no_such_tool"}]
        resp = client.post("/api/studio/validate/creature", json={"config": bad_config})
        assert resp.status_code == 200
        bad_result = resp.json()
        assert bad_result["ok"] is False
        assert [e["code"] for e in bad_result["errors"]] == ["unknown_builtin_tool"]

        # 6. Sessions: start a live session from the workspace creature
        #    directory (frontend: agentAPI.create with the creature path).
        creature_dir = workspace_root / "creatures" / "scout"
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

        # It shows up in the canonical active-session list.
        resp = client.get("/api/sessions/active")
        assert resp.status_code == 200
        assert [s["session_id"] for s in resp.json()] == [session_id]

        # Rename the creature through the session-creature rename route;
        # the active-session read reflects the new display name. Rename
        # it back to ``scout`` so the saved-session assertions below
        # (which expect ``agents == ["scout"]``) still hold.
        resp = client.post(
            f"/api/sessions/active/{session_id}/creatures/{creature_id}/rename",
            json={"name": "scout-renamed"},
        )
        assert resp.status_code == 200
        resp = client.get(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["creatures"][0]["name"] == "scout-renamed"
        resp = client.post(
            f"/api/sessions/active/{session_id}/creatures/{creature_id}/rename",
            json={"name": "scout"},
        )
        assert resp.status_code == 200
        # Renaming a creature that does not exist is a hard 404.
        resp = client.post(
            "/api/sessions/active/agents/no-such-creature/rename",
            json={"name": "x"},
        )
        assert resp.status_code == 404

        # 7. Sessions: take one chat turn over the HTTP fallback route.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/chat",
            json={"message": "ping scout"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"response": _REPLY_ONE}
        assert scripted_llm.call_count == 1

        # 8. Sessions: history reflects the turn we just took.
        resp = client.get(f"/api/sessions/{session_id}/creatures/{creature_id}/history")
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        roles = [m.get("role") for m in messages]
        assert "user" in roles and "assistant" in roles
        joined = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in messages
        )
        assert "ping scout" in joined
        assert _REPLY_ONE in joined

        # 9. Per-creature state surface — scratchpad / triggers / env /
        #    system-prompt / working-dir, the panels the Inspector reads.
        base = f"/api/sessions/{session_id}/creatures/{creature_id}"
        resp = client.get(f"{base}/scratchpad")
        assert resp.status_code == 200
        # Patch two keys, then read them back exactly — and delete one.
        resp = client.patch(
            f"{base}/scratchpad",
            json={"updates": {"phase": "scouting", "owner": "scout"}},
        )
        assert resp.status_code == 200
        assert resp.json()["phase"] == "scouting"
        assert resp.json()["owner"] == "scout"
        resp = client.patch(f"{base}/scratchpad", json={"updates": {"owner": None}})
        assert resp.status_code == 200
        assert "owner" not in resp.json()
        assert resp.json()["phase"] == "scouting"
        # A reserved scratchpad key is a hard 400.
        resp = client.patch(
            f"{base}/scratchpad", json={"updates": {"__turn_count__": "9"}}
        )
        assert resp.status_code == 400
        resp = client.get(f"{base}/triggers")
        assert resp.status_code == 200
        assert resp.json() == []
        resp = client.get(f"{base}/system-prompt")
        assert resp.status_code == 200
        assert "scout" in resp.json()["text"].lower()
        resp = client.get(f"{base}/env")
        assert resp.status_code == 200
        env_payload = resp.json()
        assert env_payload["pwd"]
        assert not any("secret" in k.lower() for k in env_payload["env"])
        resp = client.get(f"{base}/working-dir")
        assert resp.status_code == 200
        assert resp.json()["pwd"] == env_payload["pwd"]
        resp = client.get(f"{base}/native-tool-options")
        assert resp.status_code == 200
        assert resp.json() == {"tools": []}

        # 10. Per-creature plugin surface — the modules pane reads.
        resp = client.get(f"{base}/plugins")
        assert resp.status_code == 200
        plugin_names = {p["name"] for p in resp.json()}
        assert "sandbox" in plugin_names
        assert all(p["enabled"] is False for p in resp.json())
        # The HTTP toggle takes an explicit ``enabled`` — turn it on,
        # confirm via the list, then turn it back off.
        resp = client.post(f"{base}/plugins/sandbox/toggle", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json() == {"plugin": "sandbox", "enabled": True}
        resp = client.get(f"{base}/plugins")
        sandbox_entry = next(p for p in resp.json() if p["name"] == "sandbox")
        assert sandbox_entry["enabled"] is True
        resp = client.post(f"{base}/plugins/sandbox/toggle", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json() == {"plugin": "sandbox", "enabled": False}
        # An unknown plugin is a hard 404.
        resp = client.post(
            f"{base}/plugins/no-such-plugin/toggle", json={"enabled": True}
        )
        assert resp.status_code == 404

        # 11. Per-creature control — jobs list is empty, interrupt is a
        #     clean no-op on an idle creature.
        resp = client.get(f"{base}/jobs")
        assert resp.status_code == 200
        assert resp.json() == []
        resp = client.post(f"{base}/interrupt")
        assert resp.status_code == 200
        assert resp.json() == {"status": "interrupted"}
        # Cancelling a non-existent job is a hard 404.
        resp = client.post(f"{base}/tasks/no-such-job/stop")
        assert resp.status_code == 404

        # 12. A second chat turn, then regenerate the tail reply.
        resp = client.post(f"{base}/chat", json={"message": "ping again"})
        assert resp.status_code == 200
        assert resp.json() == {"response": _REPLY_TWO}
        resp = client.post(f"{base}/regenerate")
        assert resp.status_code == 200
        # The regeneration replaced the tail assistant message — history
        # reflects the new reply, the two user turns are untouched.
        resp = client.get(f"{base}/history")
        assert resp.status_code == 200
        regen_msgs = resp.json()["messages"]
        assert [m["content"] for m in regen_msgs if m["role"] == "user"] == [
            "ping scout",
            "ping again",
        ]
        assert regen_msgs[-1]["role"] == "assistant"
        # The branches route is reachable and returns a list payload.
        resp = client.get(f"{base}/branches")
        assert resp.status_code == 200
        assert resp.json() == []

        # Stop the session — flushes + closes the .kohakutr file.
        resp = client.delete(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert client.get("/api/sessions/active").json() == []

        # 13. The stopped session is now a saved session on disk — the
        #     saved-session list + history routes read it back. (No
        #     delete in this journey, so the read-only reopens here do
        #     not race a file removal.)
        resp = client.get("/api/sessions", params={"refresh": "true"})
        assert resp.status_code == 200
        listing = resp.json()
        assert listing["total"] == 1
        saved_name = listing["sessions"][0]["name"]
        assert listing["sessions"][0]["agents"] == ["scout"]
        # History index lists the agent target; the per-target read
        # returns its saved metadata.
        resp = client.get(f"/api/sessions/{saved_name}/history")
        assert resp.status_code == 200
        assert "scout" in resp.json()["targets"]
        resp = client.get(f"/api/sessions/{saved_name}/history/scout")
        assert resp.status_code == 200
        assert resp.json()["meta"]["agents"] == ["scout"]
        # An unknown target is a hard 404.
        resp = client.get(f"/api/sessions/{saved_name}/history/no-such-target")
        assert resp.status_code == 404
        # A jsonl export streams one event per line.
        resp = client.get(
            f"/api/sessions/{saved_name}/export", params={"format": "jsonl"}
        )
        assert resp.status_code == 200
        assert resp.text.strip().count("\n") >= 1

    def test_persistence_viewer_and_attach_journey(
        self,
        client: TestClient,
        workspace_root: Path,
        scripted_llm: ScriptedLLM,
    ) -> None:
        """Run a turn → saved-session list → viewer panes → fork → diff
        → resume → attach hints.

        Mirrors the studio persistence surface: ``sessionAPI.list`` /
        ``getTree`` / ``getSummary`` / ``getTurns`` / ``getEvents`` /
        ``getDiff`` / ``resume``, ``persistence/fork``, and the
        ``attachAPI`` policy-hint reads the Inspector Overview makes.
        """
        # Scaffold a workspace creature and start a session from it.
        _scaffold_workspace_creature(client, workspace_root, "viewer")
        creature_dir = workspace_root / "creatures" / "viewer"
        resp = client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature_dir)},
        )
        assert resp.status_code == 200
        created = resp.json()
        session_id = created["session_id"]
        creature_id = created["agent_id"]

        # Attach policy hints — the live creature + session advertise
        # the policy codes the Inspector Overview renders. The session
        # (whole graph) supports a superset of any one creature's
        # policies — graph-level codes (``io`` / ``observer``) are added
        # on top of the per-creature ones.
        resp = client.get(f"/api/attach/policies/{creature_id}")
        assert resp.status_code == 200
        creature_policies = resp.json()["policies"]
        assert isinstance(creature_policies, list) and creature_policies
        resp = client.get(f"/api/attach/session_policies/{session_id}")
        assert resp.status_code == 200
        session_policies = resp.json()["policies"]
        assert set(creature_policies) <= set(session_policies)
        # A creature that does not exist has no policy hints — 404.
        resp = client.get("/api/attach/policies/no-such-creature")
        assert resp.status_code == 404

        # Take a turn so the .kohakutr session store fills.
        resp = client.post(
            f"/api/sessions/{session_id}/creatures/{creature_id}/chat",
            json={"message": "remember this turn"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"response": _REPLY_ONE}

        # Stop the live session first — on Windows the SQLite handle
        # must be released before the viewer / resume re-open the file.
        resp = client.delete(f"/api/sessions/active/{session_id}")
        assert resp.status_code == 200
        assert client.get("/api/sessions/active").json() == []

        # Saved-session list (frontend: sessionAPI.list with refresh).
        resp = client.get("/api/sessions", params={"refresh": "true"})
        assert resp.status_code == 200
        listing = resp.json()
        assert listing["total"] == 1
        saved_name = listing["sessions"][0]["name"]
        assert listing["sessions"][0]["agents"] == ["viewer"]
        assert listing["sessions"][0]["preview"] == "remember this turn"

        # Viewer: tree pane — no fork lineage yet, exactly one node.
        resp = client.get(f"/api/sessions/{saved_name}/tree")
        assert resp.status_code == 200
        tree = resp.json()
        assert [n["id"] for n in tree["nodes"]] == [tree["session_id"]]
        assert tree["edges"] == []

        # Viewer: summary pane — one turn recorded for the one agent.
        resp = client.get(f"/api/sessions/{saved_name}/summary")
        assert resp.status_code == 200
        summary = resp.json()
        assert summary["agents"] == ["viewer"]
        assert summary["config_type"] == "agent"
        assert summary["totals"]["turns"] == 1

        # Viewer: turns pane — the single turn row is present.
        resp = client.get(f"/api/sessions/{saved_name}/turns")
        assert resp.status_code == 200
        turns = resp.json()
        assert len(turns["turns"]) == 1

        # Viewer: events pane — the user_input event carries our text.
        resp = client.get(
            f"/api/sessions/{saved_name}/events", params={"types": "user_input"}
        )
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert events
        assert all(e["type"] == "user_input" for e in events)
        event_blob = str(events)
        assert "remember this turn" in event_blob

        # Viewer: export pane — every supported format renders the turn.
        resp = client.get(f"/api/sessions/{saved_name}/export", params={"format": "md"})
        assert resp.status_code == 200
        assert "remember this turn" in resp.text
        resp = client.get(
            f"/api/sessions/{saved_name}/export", params={"format": "html"}
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "remember this turn" in resp.text
        assert "<!doctype html>" in resp.text.lower()
        resp = client.get(
            f"/api/sessions/{saved_name}/export", params={"format": "jsonl"}
        )
        assert resp.status_code == 200
        assert resp.text.strip().count("\n") >= 1
        # An unknown export format is a hard 400.
        resp = client.get(
            f"/api/sessions/{saved_name}/export", params={"format": "pdf"}
        )
        assert resp.status_code == 400

        # Viewer: turns pane with explicit pagination bounds.
        resp = client.get(
            f"/api/sessions/{saved_name}/turns",
            params={"limit": "10", "offset": "0"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        # Saved-session aggregates — disk usage + stats both see the
        # one recorded session.
        resp = client.get("/api/sessions/disk-usage")
        assert resp.status_code == 200
        resp = client.get("/api/sessions/stats")
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

        # Workspace-files surface — the file-tree / read / write / mkdir
        # / rename / delete routes the editor's file browser drives.
        # They run over the real on-disk workspace directory.
        resp = client.get(
            "/api/files/tree", params={"root": str(workspace_root), "depth": "2"}
        )
        assert resp.status_code == 200
        tree_root = resp.json()
        assert tree_root["type"] == "directory"
        assert tree_root["has_children"] is True
        child_names = {c["name"] for c in tree_root["children"]}
        assert "creatures" in child_names
        # browse_directories returns the current dir + its subdirs.
        resp = client.get("/api/files/browse", params={"path": str(workspace_root)})
        assert resp.status_code == 200
        browse = resp.json()
        assert browse["current"]["path"] == str(workspace_root)
        assert "creatures" in {d["name"] for d in browse["directories"]}
        # write a file, read it back with metadata, then mutate it.
        note_path = workspace_root / "notes.md"
        resp = client.post(
            "/api/files/write",
            json={"path": str(note_path), "content": "# notes\nfirst"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        resp = client.get("/api/files/read", params={"path": str(note_path)})
        assert resp.status_code == 200
        read_payload = resp.json()
        assert read_payload["content"] == "# notes\nfirst"
        assert read_payload["language"] == "markdown"
        # mkdir, rename the note into it, then delete the whole subtree.
        sub_dir = workspace_root / "docs"
        resp = client.post("/api/files/mkdir", json={"path": str(sub_dir)})
        assert resp.status_code == 200
        moved = sub_dir / "notes.md"
        resp = client.post(
            "/api/files/rename",
            json={"old_path": str(note_path), "new_path": str(moved)},
        )
        assert resp.status_code == 200
        assert moved.exists() and not note_path.exists()
        resp = client.post("/api/files/delete", json={"path": str(sub_dir)})
        assert resp.status_code == 200
        assert not sub_dir.exists()
        # Reading a path that does not exist is a hard 404.
        resp = client.get(
            "/api/files/read", params={"path": str(workspace_root / "ghost.md")}
        )
        assert resp.status_code == 404
        # mkdir over an existing directory is a hard 400.
        resp = client.post(
            "/api/files/mkdir", json={"path": str(workspace_root / "creatures")}
        )
        assert resp.status_code == 400

        # Fork the saved session at its last event into a new .kohakutr.
        resp = client.get(
            f"/api/sessions/{saved_name}/events", params={"limit": "1000"}
        )
        assert resp.status_code == 200
        all_events = resp.json()["events"]
        last_event_id = max(e["event_id"] for e in all_events)
        resp = client.post(
            f"/api/sessions/{saved_name}/fork",
            json={"at_event_id": last_event_id, "name": "viewer-fork"},
        )
        assert resp.status_code == 201
        fork = resp.json()
        assert fork["fork_point"] == last_event_id
        assert Path(fork["path"]).exists()

        # The fork is now a second saved session in the listing.
        resp = client.get("/api/sessions", params={"refresh": "true"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 2
        names = {s["name"] for s in resp.json()["sessions"]}
        fork_name = next(n for n in names if n != saved_name)

        # The fork's tree pane records its lineage back to the parent.
        resp = client.get(f"/api/sessions/{fork_name}/tree")
        assert resp.status_code == 200
        fork_tree = resp.json()
        assert len(fork_tree["nodes"]) >= 1

        # Diff the parent against the fork — a structured comparison
        # sliced to the one agent on each side. The fork was taken at
        # the parent's last event, so neither side diverges.
        resp = client.get(
            f"/api/sessions/{saved_name}/diff", params={"other": fork_name}
        )
        assert resp.status_code == 200
        diff = resp.json()
        assert diff["a"]["agent"] == "viewer"
        assert diff["b"]["agent"] == "viewer"
        assert diff["identical"] is True

        # Resume the parent session from disk into the engine.
        resp = client.post(f"/api/sessions/{saved_name}/resume")
        assert resp.status_code == 200
        resumed = resp.json()
        assert resumed["type"] == "agent"
        assert resumed["session_name"] == "viewer"
        resumed_id = resumed["instance_id"]

        # The resumed session is live again in the active list.
        resp = client.get("/api/sessions/active")
        assert resp.status_code == 200
        assert [s["session_id"] for s in resp.json()] == [resumed_id]

        # Stop it, then delete both saved files off disk.
        resp = client.delete(f"/api/sessions/active/{resumed_id}")
        assert resp.status_code == 200
        resp = client.delete(f"/api/sessions/{saved_name}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        resp = client.delete(f"/api/sessions/{fork_name}")
        assert resp.status_code == 200

        # Both gone from the saved-session listing after a rebuild.
        resp = client.get("/api/sessions", params={"refresh": "true"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


def test_session_dir_env_isolation(client: TestClient, tmp_path: Path) -> None:
    """Sanity guard: the fixture really redirected ``KT_SESSION_DIR``
    into ``tmp_path`` so the journey never touches the user's real
    session directory."""
    assert os.environ["KT_SESSION_DIR"].startswith(str(tmp_path))
