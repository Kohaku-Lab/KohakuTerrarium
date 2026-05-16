"""More catalog route tests — builtins, modules, skills, validate."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.catalog import (
    _deps as catalog_deps,
    builtins as builtins_mod,
    modules as modules_mod,
    skills as skills_mod,
    validate as validate_mod,
)

PREFIX = "/x"


def _client(router) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=PREFIX)
    return TestClient(app)


@pytest.fixture
def _workspace(tmp_path):
    from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace

    ws = LocalWorkspace.open(str(tmp_path))
    catalog_deps.set_workspace(ws)
    yield ws
    catalog_deps.set_workspace(None)


# ── /builtins ─────────────────────────────────────────────────


class TestBuiltinsRoute:
    def test_list_tools(self):
        catalog_deps.set_workspace(None)
        r = _client(builtins_mod.router).get(PREFIX + "/tools")
        assert r.status_code == 200
        names = {e["name"] for e in r.json()}
        # The general builtin tool set documented in CLAUDE.md must be
        # present, plus the privileged group_* surface.
        assert {"read", "write", "edit", "bash", "glob", "grep"} <= names
        assert {"group_add_node", "group_channel"} <= names
        # Entries are sorted by name.
        sorted_names = [e["name"] for e in r.json()]
        assert sorted_names == sorted(sorted_names)

    def test_list_tools_with_workspace(self, _workspace):
        # An empty workspace contributes nothing — the builtin set is
        # still returned in full.
        r = _client(builtins_mod.router).get(PREFIX + "/tools")
        assert r.status_code == 200
        names = {e["name"] for e in r.json()}
        assert {"read", "write", "bash"} <= names

    def test_get_tool_doc_unknown(self):
        r = _client(builtins_mod.router).get(PREFIX + "/tools/ghost/doc")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "not_found"

    def test_get_tool_doc_known(self):
        # ``bash`` is a documented builtin — its doc must resolve.
        catalog_deps.set_workspace(None)
        r2 = _client(builtins_mod.router).get(PREFIX + "/tools/bash/doc")
        assert r2.status_code == 200
        body = r2.json()
        assert body["name"] == "bash"
        assert body["doc"]  # non-empty documentation string

    def test_list_subagents(self):
        catalog_deps.set_workspace(None)
        r = _client(builtins_mod.router).get(PREFIX + "/subagents")
        assert r.status_code == 200
        names = {e["name"] for e in r.json()}
        # Built-in sub-agents documented in CLAUDE.md.
        assert {
            "coordinator",
            "critic",
            "explore",
            "plan",
            "research",
            "response",
            "memory_read",
            "memory_write",
        } <= names

    def test_get_subagent_doc_unknown(self):
        r = _client(builtins_mod.router).get(PREFIX + "/subagents/ghost/doc")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "not_found"

    def test_get_subagent_doc_known(self):
        catalog_deps.set_workspace(None)
        r = _client(builtins_mod.router).get(PREFIX + "/subagents/critic/doc")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "critic"
        assert body["doc"]

    def test_list_triggers(self):
        catalog_deps.set_workspace(None)
        r = _client(builtins_mod.router).get(PREFIX + "/triggers")
        assert r.status_code == 200
        names = {e["name"] for e in r.json()}
        assert {"add_timer", "watch_channel", "add_schedule"} <= names

    def test_list_plugins(self):
        # There are no *builtin* plugins — the list is purely workspace +
        # package contributions, deduped and sorted by name. With no
        # workspace open, every entry must therefore be package-sourced.
        catalog_deps.set_workspace(None)
        r = _client(builtins_mod.router).get(PREFIX + "/plugins")
        assert r.status_code == 200
        body = r.json()
        names = [e["name"] for e in body]
        assert names == sorted(names)
        assert all(e["source"].startswith("package:") for e in body)

    def test_list_inputs(self):
        catalog_deps.set_workspace(None)
        r = _client(builtins_mod.router).get(PREFIX + "/inputs")
        assert r.status_code == 200
        body = r.json()
        names = [e["name"] for e in body]
        assert names == sorted(names)
        assert all(e["source"].startswith("package:") for e in body)

    def test_list_outputs(self):
        catalog_deps.set_workspace(None)
        r = _client(builtins_mod.router).get(PREFIX + "/outputs")
        assert r.status_code == 200
        body = r.json()
        names = [e["name"] for e in body]
        assert names == sorted(names)
        assert all(e["source"].startswith("package:") for e in body)

    def test_list_models(self):
        r = _client(builtins_mod.router).get(PREFIX + "/models")
        assert r.status_code == 200
        from kohakuterrarium.llm.profiles import list_all as list_all_models

        # Route is a thin pass-through to llm.profiles.list_all.
        assert r.json() == list_all_models()

    def test_list_embedding_presets(self):
        r = _client(builtins_mod.router).get(PREFIX + "/embedding_presets")
        assert r.status_code == 200
        body = r.json()
        # Grouped by embedder family.
        assert set(body.keys()) == {"model2vec", "sentence-transformer"}

    def test_list_plugin_hooks(self):
        r = _client(builtins_mod.router).get(PREFIX + "/plugin_hooks")
        assert r.status_code == 200
        from kohakuterrarium.studio.editors.plugin_hooks import PLUGIN_HOOKS

        assert r.json() == PLUGIN_HOOKS


# ── /modules ──────────────────────────────────────────────────


class TestModulesRoute:
    def test_list_unknown_kind(self, _workspace):
        r = _client(modules_mod.router).get(PREFIX + "/not-a-kind")
        assert r.status_code == 400

    def test_list_tools(self, _workspace):
        r = _client(modules_mod.router).get(PREFIX + "/tools")
        assert r.status_code == 200
        assert r.json() == []

    def test_load_unknown(self, _workspace):
        r = _client(modules_mod.router).get(PREFIX + "/tools/ghost")
        assert r.status_code == 404

    def test_scaffold_invalid_kind(self, _workspace):
        r = _client(modules_mod.router).post(PREFIX + "/not-a-kind", json={"name": "x"})
        assert r.status_code == 400

    def test_save_unknown_kind(self, _workspace):
        r = _client(modules_mod.router).put(
            PREFIX + "/not-a-kind/foo", json={"mode": "simple", "form": {}}
        )
        assert r.status_code == 400

    def test_load_doc_unknown(self, _workspace):
        # ``tools`` is a valid kind, so the handler runs and reports the
        # missing module — 404, not a 400 kind-rejection.
        r = _client(modules_mod.router).get(PREFIX + "/tools/ghost/doc")
        assert r.status_code == 404

    def test_save_doc_unknown(self, _workspace):
        r = _client(modules_mod.router).put(
            PREFIX + "/tools/ghost/doc", json={"content": ""}
        )
        assert r.status_code == 404

    def test_delete_requires_confirm(self, _workspace):
        r = _client(modules_mod.router).delete(PREFIX + "/tools/ghost")
        assert r.status_code == 428

    def test_delete_unknown_with_confirm(self, _workspace):
        r = _client(modules_mod.router).delete(PREFIX + "/tools/ghost?confirm=true")
        assert r.status_code == 404

    def test_scaffold_then_list_load_save_doc_delete_roundtrip(self, _workspace):
        # The full module CRUD lifecycle observed end-to-end against a
        # real LocalWorkspace — every mutation is verified by a
        # follow-up read.
        client = _client(modules_mod.router)

        # Scaffold a fresh tool → 201, and it becomes listable.
        r = client.post(PREFIX + "/tools", json={"name": "my_tool"})
        assert r.status_code == 201
        assert r.json()["name"] == "my_tool"
        listed = {m["name"] for m in client.get(PREFIX + "/tools").json()}
        assert "my_tool" in listed

        # Load it back — the envelope carries the parsed form + raw source.
        loaded = client.get(PREFIX + "/tools/my_tool")
        assert loaded.status_code == 200
        body = loaded.json()
        assert body["name"] == "my_tool"
        raw = body["raw_source"]
        assert "my_tool" in raw

        # Save (raw mode round-trips the source unchanged) → still loadable.
        saved = client.put(
            PREFIX + "/tools/my_tool",
            json={"mode": "raw", "form": {}, "execute_body": "", "raw_source": raw},
        )
        assert saved.status_code == 200
        assert saved.json()["name"] == "my_tool"

        # Doc starts empty; saving it makes the read return the content.
        d0 = client.get(PREFIX + "/tools/my_tool/doc")
        assert d0.status_code == 200
        assert d0.json()["content"] == ""
        dsave = client.put(
            PREFIX + "/tools/my_tool/doc", json={"content": "# How to my_tool"}
        )
        assert dsave.status_code == 200
        d1 = client.get(PREFIX + "/tools/my_tool/doc")
        assert d1.json()["content"] == "# How to my_tool"

        # Delete with confirm → 200, and it's gone from the listing.
        dele = client.delete(PREFIX + "/tools/my_tool?confirm=true")
        assert dele.status_code == 200
        assert dele.json() == {"ok": True}
        assert "my_tool" not in {
            m["name"] for m in client.get(PREFIX + "/tools").json()
        }

    def test_scaffold_duplicate_conflicts(self, _workspace):
        client = _client(modules_mod.router)
        first = client.post(PREFIX + "/tools", json={"name": "dup_tool"})
        assert first.status_code == 201
        # Re-scaffolding the same name → 409 name_exists.
        again = client.post(PREFIX + "/tools", json={"name": "dup_tool"})
        assert again.status_code == 409
        assert again.json()["detail"]["code"] == "name_exists"

    def test_save_doc_for_missing_module_404s(self, _workspace):
        # Doc save requires the module to exist first — the route says
        # so in its error message.
        r = _client(modules_mod.router).put(
            PREFIX + "/tools/never_made/doc", json={"content": "x"}
        )
        assert r.status_code == 404
        assert "create the module first" in r.json()["detail"]["message"]

    def test_load_invalid_name_400(self, _workspace):
        # A dot-prefixed module name trips the sanitiser → 400
        # invalid_name (not a 404).
        r = _client(modules_mod.router).get(PREFIX + "/tools/.bad")
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_name"

    def test_scaffold_invalid_name_400(self, _workspace):
        r = _client(modules_mod.router).post(PREFIX + "/tools", json={"name": ".bad"})
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_name"

    def test_load_doc_invalid_name_400(self, _workspace):
        r = _client(modules_mod.router).get(PREFIX + "/tools/.bad/doc")
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_name"

    def test_delete_invalid_name_400(self, _workspace):
        r = _client(modules_mod.router).delete(PREFIX + "/tools/.bad?confirm=true")
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_name"

    def test_save_doc_invalid_name_400(self, _workspace):
        # A dot-prefixed name on the doc-save endpoint trips the
        # sanitiser → 400 invalid_name (not the 404 missing-module path).
        r = _client(modules_mod.router).put(
            PREFIX + "/tools/.bad/doc", json={"content": "x"}
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_name"

    def test_save_raw_mode_empty_source_400(self, _workspace):
        # raw mode requires a non-empty raw_source — the workspace
        # rejects it with ValueError → route 400 invalid_input.
        client = _client(modules_mod.router)
        client.post(PREFIX + "/tools", json={"name": "rawtool"})
        r = client.put(
            PREFIX + "/tools/rawtool",
            json={"mode": "raw", "form": {}, "execute_body": "", "raw_source": ""},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_input"

    def test_save_unknown_mode_400(self, _workspace):
        client = _client(modules_mod.router)
        client.post(PREFIX + "/tools", json={"name": "modetool"})
        r = client.put(
            PREFIX + "/tools/modetool",
            json={"mode": "nonsense", "form": {}, "execute_body": ""},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_input"

    def test_save_simple_mode_roundtrip_failure_422(self, _workspace, tmp_path):
        # If the on-disk module has no patchable class, simple-mode save
        # can't round-trip → the route surfaces 422 roundtrip_failed.
        client = _client(modules_mod.router)
        client.post(PREFIX + "/tools", json={"name": "rttool"})
        # Corrupt the scaffolded file so update_existing has no class.
        from kohakuterrarium.api.routes.catalog import _deps as cd

        ws = cd.get_workspace()
        py = ws.module_kind_dir("tools") / "rttool.py"
        py.write_text("# no class here\nx = 1\n", encoding="utf-8")
        r = client.put(
            PREFIX + "/tools/rttool",
            json={
                "mode": "simple",
                "form": {
                    "class_name": "RtTool",
                    "tool_name": "rttool",
                    "description": "d",
                    "execution_mode": "direct",
                },
                "execute_body": "return None",
            },
        )
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "roundtrip_failed"


# ── /skills ───────────────────────────────────────────────────


class TestSkillsRoute:
    def test_list_skills_no_workspace(self):
        # With no workspace open, the skills catalog still surfaces
        # installed-package skills; each entry carries the contract
        # fields the frontend's skills pane reads.
        catalog_deps.set_workspace(None)
        r = _client(skills_mod.router).get(PREFIX)
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        for entry in body:
            assert {"name", "description", "origin", "enabled"} <= set(entry)

    def test_list_skills_with_workspace_adds_workspace_skill(
        self, _workspace, tmp_path
    ):
        # A project skill under the open workspace's .kt/skills dir must
        # be discovered and surface as an enabled "project"-origin entry.
        skill_dir = tmp_path / ".kt" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: a test skill\n---\nbody"
        )
        r = _client(skills_mod.router).get(PREFIX)
        assert r.status_code == 200
        by_name = {e["name"]: e for e in r.json()}
        assert "my-skill" in by_name
        assert by_name["my-skill"]["origin"] == "project"
        assert by_name["my-skill"]["enabled"] is True

    def test_toggle_unknown_skill(self):
        catalog_deps.set_workspace(None)
        r = _client(skills_mod.router).post(PREFIX + "/definitely-not-a-skill/toggle")
        assert r.status_code == 404

    def test_toggle_flips_persisted_enabled_state(self, _workspace, tmp_path):
        # A project skill starts enabled; one toggle flips it off, a
        # second flips it back on — the state round-trips through the
        # persisted skills-state file.
        skill_dir = tmp_path / ".kt" / "skills" / "flip-me"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: flip-me\ndescription: toggle target\n---\nbody"
        )
        client = _client(skills_mod.router)
        # Initially discovered as enabled.
        before = {e["name"]: e for e in client.get(PREFIX).json()}
        assert before["flip-me"]["enabled"] is True
        # First toggle → disabled, and the response says so.
        r1 = client.post(PREFIX + "/flip-me/toggle")
        assert r1.status_code == 200
        assert r1.json() == {"name": "flip-me", "enabled": False}
        # The list reflects the new persisted state.
        assert {e["name"]: e for e in client.get(PREFIX).json()}["flip-me"][
            "enabled"
        ] is False
        # Second toggle → back to enabled.
        r2 = client.post(PREFIX + "/flip-me/toggle")
        assert r2.json() == {"name": "flip-me", "enabled": True}

    def test_list_skills_discovery_failure_500(self, _workspace, monkeypatch):
        # If the filesystem skill scan blows up, the route surfaces a
        # structured 500 (discovery_failed), not an unhandled crash.
        def _boom(cwd):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(skills_mod, "_discover_with_state", _boom)
        r = _client(skills_mod.router).get(PREFIX)
        assert r.status_code == 500
        assert r.json()["detail"]["code"] == "discovery_failed"

    def test_toggle_skill_discovery_failure_500(self, monkeypatch):
        # A non-FileNotFoundError during toggle → structured 500.
        catalog_deps.set_workspace(None)

        def _boom(cwd, name):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(skills_mod, "_toggle_skill_sync", _boom)
        r = _client(skills_mod.router).post(PREFIX + "/anything/toggle")
        assert r.status_code == 500
        assert r.json()["detail"]["code"] == "discovery_failed"


# ── /validate ─────────────────────────────────────────────────


class TestValidateRoute:
    def test_validate_creature_minimal(self, _workspace):
        r = _client(validate_mod.router).post(PREFIX + "/creature", json={"config": {}})
        assert r.status_code == 200
        body = r.json()
        assert "ok" in body
        assert "errors" in body

    def test_validate_creature_schema_error(self, _workspace):
        # Pass garbage that will trip pydantic.
        r = _client(validate_mod.router).post(
            PREFIX + "/creature",
            json={"config": {"tools": "should-be-list"}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["errors"]

    def test_validate_creature_unknown_builtin_tool(self, _workspace):
        r = _client(validate_mod.router).post(
            PREFIX + "/creature",
            json={
                "config": {
                    "name": "x",
                    "tools": [{"type": "builtin", "name": "no-such-tool"}],
                }
            },
        )
        body = r.json()
        assert any(err["code"] == "unknown_builtin_tool" for err in body["errors"])

    def test_validate_creature_custom_tool_missing_module(self, _workspace):
        r = _client(validate_mod.router).post(
            PREFIX + "/creature",
            json={
                "config": {
                    "name": "x",
                    "tools": [{"type": "custom", "name": "x"}],
                }
            },
        )
        body = r.json()
        assert any(err["code"] == "missing_module" for err in body["errors"])

    def test_validate_creature_unknown_subagent(self, _workspace):
        r = _client(validate_mod.router).post(
            PREFIX + "/creature",
            json={
                "config": {
                    "name": "x",
                    "subagents": [{"type": "builtin", "name": "no-such-sa"}],
                }
            },
        )
        body = r.json()
        assert any(err["code"] == "unknown_builtin_subagent" for err in body["errors"])

    def test_validate_creature_absolute_prompt_path(self, _workspace):
        r = _client(validate_mod.router).post(
            PREFIX + "/creature",
            json={"config": {"name": "x", "system_prompt_file": "/abs/path.md"}},
        )
        body = r.json()
        assert any(err["code"] == "absolute_prompt_path" for err in body["errors"])

    def test_validate_module_unknown_kind(self):
        r = _client(validate_mod.router).post(
            PREFIX + "/module", json={"kind": "no", "source": "x = 1"}
        )
        assert r.status_code == 400

    def test_validate_module_syntax_error(self):
        r = _client(validate_mod.router).post(
            PREFIX + "/module",
            json={"kind": "tools", "source": "def broken(:\n"},
        )
        body = r.json()
        assert body["ok"] is False
        assert body["errors"][0]["code"] == "syntax_error"

    def test_validate_module_ok(self):
        r = _client(validate_mod.router).post(
            PREFIX + "/module",
            json={"kind": "tools", "source": "x = 1\n"},
        )
        body = r.json()
        assert body["ok"] is True
        assert body["errors"] == []
