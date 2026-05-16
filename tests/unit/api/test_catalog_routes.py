"""Unit tests for ``api.routes.catalog.*`` routes.

Each router is mounted on its own FastAPI app via ``TestClient`` under
a synthetic ``/x`` prefix so FastAPI doesn't reject the empty path.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.catalog import (
    _deps as catalog_deps,
    commands as commands_mod,
    creatures as creatures_mod,
    creatures_scan as creatures_scan_mod,
    manifest as manifest_mod,
    models as models_mod,
    packages as packages_mod,
    registry as registry_mod,
    schema as schema_mod,
    server_info as server_info_mod,
    templates as templates_mod,
    terrariums_scan as terrariums_scan_mod,
    workspace as workspace_mod,
)

PREFIX = "/x"


def _client(router) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=PREFIX)
    return TestClient(app)


# ── /commands ─────────────────────────────────────────────────


class TestCommandsRoute:
    def test_list_commands(self):
        r = _client(commands_mod.router).get(PREFIX)
        assert r.status_code == 200
        body = r.json()
        names = {c["name"] for c in body}
        # The builtin slash commands documented in CLAUDE.md.
        assert {
            "clear",
            "compact",
            "exit",
            "help",
            "model",
            "plugin",
            "regen",
            "status",
        } <= names
        # Each entry carries the four fields the frontend palette reads.
        for c in body:
            assert {"name", "aliases", "description", "layer"} == set(c)


# ── /models ────────────────────────────────────────────────────


class TestModelsRoute:
    def test_list_models(self):
        r = _client(models_mod.router).get(PREFIX)
        assert r.status_code == 200
        from kohakuterrarium.llm.profiles import list_all

        # Route is a thin pass-through to llm.profiles.list_all.
        assert r.json() == list_all()


# ── /registry ──────────────────────────────────────────────────


class TestRegistryRoute:
    def test_list_remote(self):
        r = _client(registry_mod.router).get(PREFIX)
        assert r.status_code == 200
        from kohakuterrarium.studio.catalog.packages_remote import (
            load_remote_registry,
        )

        # Route is a thin pass-through to load_remote_registry.
        assert r.json() == load_remote_registry()


# ── /server_info ───────────────────────────────────────────────


class TestServerInfoRoute:
    def test_server_info(self):
        import os
        import sys

        r = _client(server_info_mod.router).get(PREFIX)
        assert r.status_code == 200
        # Route reports the live process cwd + platform verbatim.
        assert r.json() == {"cwd": os.getcwd(), "platform": sys.platform}


# ── /creatures_scan ────────────────────────────────────────────


class TestCreaturesScanRoute:
    def test_empty_dirs(self):
        from kohakuterrarium.studio.catalog.packages_scan import (
            invalidate_scan_caches,
        )

        creatures_scan_mod.set_creatures_dirs([])
        invalidate_scan_caches()
        r = _client(creatures_scan_mod.router).get(PREFIX)
        assert r.status_code == 200
        # No configured dirs → nothing to scan.
        assert r.json() == []

    def test_set_dirs_then_scan(self, tmp_path):
        from kohakuterrarium.studio.catalog.packages_scan import (
            invalidate_scan_caches,
        )

        # The scanner keys on config.yaml, not agent.yaml.
        c = tmp_path / "alice"
        c.mkdir()
        (c / "config.yaml").write_text("name: alice\ndescription: a bot\n")
        creatures_scan_mod.set_creatures_dirs([str(tmp_path)])
        invalidate_scan_caches()
        try:
            r = _client(creatures_scan_mod.router).get(PREFIX)
            assert r.status_code == 200
            body = r.json()
            assert len(body) == 1
            assert body[0]["name"] == "alice"
            assert body[0]["description"] == "a bot"
        finally:
            creatures_scan_mod.set_creatures_dirs([])
            invalidate_scan_caches()


# ── /terrariums_scan ───────────────────────────────────────────


class TestTerrariumsScanRoute:
    def test_empty_dirs(self):
        from kohakuterrarium.studio.catalog.packages_scan import (
            invalidate_scan_caches,
        )

        terrariums_scan_mod.set_terrariums_dirs([])
        invalidate_scan_caches()
        r = _client(terrariums_scan_mod.router).get(PREFIX)
        assert r.status_code == 200
        assert r.json() == []

    def test_set_dirs(self, tmp_path):
        from kohakuterrarium.studio.catalog.packages_scan import (
            invalidate_scan_caches,
        )

        # Scanner keys on terrarium.yaml.
        t = tmp_path / "swarm"
        t.mkdir()
        (t / "terrarium.yaml").write_text("name: swarm\n")
        terrariums_scan_mod.set_terrariums_dirs([str(tmp_path)])
        invalidate_scan_caches()
        try:
            r = _client(terrariums_scan_mod.router).get(PREFIX)
            assert r.status_code == 200
            body = r.json()
            assert len(body) == 1
            assert body[0]["name"] == "swarm"
        finally:
            terrariums_scan_mod.set_terrariums_dirs([])
            invalidate_scan_caches()


# ── /packages ──────────────────────────────────────────────────


class TestPackagesRoute:
    def test_list_local(self):
        r = _client(packages_mod.router).get(PREFIX)
        assert r.status_code == 200
        from kohakuterrarium.studio.catalog.packages_scan import scan_catalog

        # Route projects each catalog entry through as_registry_dict.
        assert r.json() == [e.as_registry_dict() for e in scan_catalog()]

    def test_install_failure(self):
        r = _client(packages_mod.router).post(
            PREFIX + "/install", json={"url": "not-a-real-url://nowhere"}
        )
        assert r.status_code == 400

    def test_uninstall_missing(self):
        r = _client(packages_mod.router).post(
            PREFIX + "/uninstall",
            json={"name": "definitely-not-installed-xyz"},
        )
        assert r.status_code == 404

    def test_install_success(self, monkeypatch):
        # The git clone itself is true I/O — stub it. The route's
        # contract is to echo {"status": "installed", "name": <resolved>}.
        monkeypatch.setattr(
            packages_mod, "install_package_op", lambda source, name: "resolved-pkg"
        )
        r = _client(packages_mod.router).post(
            PREFIX + "/install", json={"url": "https://example.com/p.git"}
        )
        assert r.status_code == 200
        assert r.json() == {"status": "installed", "name": "resolved-pkg"}

    def test_uninstall_success(self, monkeypatch):
        monkeypatch.setattr(packages_mod, "uninstall_package_op", lambda name: True)
        r = _client(packages_mod.router).post(
            PREFIX + "/uninstall", json={"name": "some-pkg"}
        )
        assert r.status_code == 200
        assert r.json() == {"status": "uninstalled", "name": "some-pkg"}


# ── /templates ─────────────────────────────────────────────────


class TestTemplatesRoute:
    def test_list_templates(self):
        r = _client(templates_mod.router).get(PREFIX)
        assert r.status_code == 200
        # Route returns the fixed catalog of scaffolding templates.
        assert r.json() == templates_mod._TEMPLATES

    def test_render_unknown_id(self):
        r = _client(templates_mod.router).post(
            PREFIX + "/render",
            json={"id": "no-such-template", "context": {}},
        )
        assert r.status_code == 404

    def test_render_known_id(self):
        # A complete context renders the tool.py.j2 template — the
        # response carries the rendered source under "source".
        r = _client(templates_mod.router).post(
            PREFIX + "/render",
            json={
                "id": "tool-minimal",
                "context": {
                    "class_name": "MyTool",
                    "tool_name": "my_tool",
                    "description": "does a thing",
                    "execution_mode": "direct",
                    "execute_body": "return ToolResult(content='ok')",
                },
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "tool-minimal"
        # Rendered Python carries the substituted class + tool names.
        assert "class MyTool(BaseTool)" in body["source"]
        assert '"my_tool"' in body["source"]

    def test_render_incomplete_context_400(self):
        # Missing required vars (description / execute_body) → the
        # template render fails and the route reports render_failed.
        r = _client(templates_mod.router).post(
            PREFIX + "/render",
            json={
                "id": "tool-minimal",
                "context": {"class_name": "MyTool", "tool_name": "my_tool"},
            },
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "render_failed"


# ── /workspace ─────────────────────────────────────────────────


class TestWorkspaceRoute:
    def test_get_no_workspace_409(self):
        catalog_deps.set_workspace(None)
        r = _client(workspace_mod.router).get(PREFIX)
        assert r.status_code == 409

    def test_open_missing_path(self, tmp_path):
        ghost = tmp_path / "does-not-exist"
        r = _client(workspace_mod.router).post(
            PREFIX + "/open", json={"path": str(ghost)}
        )
        assert r.status_code == 400

    def test_open_file_path_400(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        r = _client(workspace_mod.router).post(PREFIX + "/open", json={"path": str(f)})
        assert r.status_code == 400

    def test_open_and_close(self, tmp_path):
        client = _client(workspace_mod.router)
        try:
            r = client.post(PREFIX + "/open", json={"path": str(tmp_path)})
            assert r.status_code == 200
            # Open returns the summary of the just-opened workspace.
            assert r.json()["root"] == str(tmp_path)
            # After open, the workspace is the active one.
            assert catalog_deps.get_workspace_optional() is not None
            r2 = client.post(PREFIX + "/close")
            assert r2.status_code == 204
            # After close, no workspace is active → GET 409s.
            assert client.get(PREFIX).status_code == 409
        finally:
            catalog_deps.set_workspace(None)

    def test_get_summary_after_open(self, tmp_path):
        client = _client(workspace_mod.router)
        try:
            client.post(PREFIX + "/open", json={"path": str(tmp_path)})
            r = client.get(PREFIX)
            assert r.status_code == 200
            # GET returns the active workspace's summary.
            assert r.json()["root"] == str(tmp_path)
            assert r.json()["creatures"] == []
        finally:
            catalog_deps.set_workspace(None)


# ── /creatures ─────────────────────────────────────────────────


@pytest.fixture
def _workspace(tmp_path):
    from kohakuterrarium.studio.editors.workspace_fs import LocalWorkspace

    ws = LocalWorkspace.open(str(tmp_path))
    catalog_deps.set_workspace(ws)
    yield ws
    catalog_deps.set_workspace(None)


class TestCreaturesRoute:
    def test_list_empty(self, _workspace):
        r = _client(creatures_mod.router).get(PREFIX)
        assert r.status_code == 200
        assert r.json() == []

    def test_load_unknown(self, _workspace):
        r = _client(creatures_mod.router).get(PREFIX + "/ghost")
        assert r.status_code == 404

    def test_scaffold(self, _workspace):
        client = _client(creatures_mod.router)
        r = client.post(
            PREFIX,
            json={"name": "alice", "base_config": None, "description": "x"},
        )
        # Fresh name in an empty workspace → created.
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "alice"
        # The scaffolded creature is now listable.
        listed = {c["name"] for c in client.get(PREFIX).json()}
        assert "alice" in listed

    def test_scaffold_duplicate(self, _workspace):
        client = _client(creatures_mod.router)
        first = client.post(PREFIX, json={"name": "dup", "description": ""})
        assert first.status_code == 201
        # Re-scaffolding the same name conflicts.
        r2 = client.post(PREFIX, json={"name": "dup", "description": ""})
        assert r2.status_code == 409

    def test_save_unknown(self, _workspace):
        # Save is an upsert — saving a not-yet-existing creature creates
        # it, then it reads back.
        client = _client(creatures_mod.router)
        r = client.put(
            PREFIX + "/never-existed",
            json={"config": {"name": "never-existed"}, "prompts": {}},
        )
        assert r.status_code == 200
        assert "never-existed" in {c["name"] for c in client.get(PREFIX).json()}

    def test_scaffold_invalid_name_400(self, _workspace):
        # A dot-prefixed creature name is rejected by the workspace
        # sanitiser → 400 invalid_name.
        r = _client(creatures_mod.router).post(
            PREFIX, json={"name": ".dotname", "description": ""}
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_name"

    def test_delete_invalid_name_400(self, _workspace):
        # ``confirm=true`` passes the gate, then the bad name trips the
        # sanitiser → 400 invalid_name (not a 404).
        r = _client(creatures_mod.router).delete(PREFIX + "/.dotname?confirm=true")
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_name"

    def test_delete_requires_confirm(self, _workspace):
        r = _client(creatures_mod.router).delete(PREFIX + "/alice")
        assert r.status_code == 428

    def test_delete_unknown_with_confirm(self, _workspace):
        r = _client(creatures_mod.router).delete(PREFIX + "/ghost?confirm=true")
        assert r.status_code == 404

    def test_read_prompt_unknown(self, _workspace):
        # ``ghost`` is a valid (safe) name that simply doesn't exist →
        # 404, not a 400 unsafe-path rejection.
        r = _client(creatures_mod.router).get(PREFIX + "/ghost/prompts/system.md")
        assert r.status_code == 404

    def test_load_then_save_then_prompt_roundtrip(self, _workspace):
        # Full creature lifecycle against a real LocalWorkspace: scaffold
        # → load → write a prompt → read it back → delete.
        client = _client(creatures_mod.router)
        assert (
            client.post(PREFIX, json={"name": "rt", "description": "x"}).status_code
            == 201
        )
        # Load returns the creature's config + prompt map.
        loaded = client.get(PREFIX + "/rt")
        assert loaded.status_code == 200
        assert loaded.json()["config"]["name"] == "rt"

        # Write a prompt file, then read it back verbatim.
        w = client.put(PREFIX + "/rt/prompts/system.md", json={"content": "be terse"})
        assert w.status_code == 200
        assert w.json() == {"ok": True, "path": "system.md"}
        rd = client.get(PREFIX + "/rt/prompts/system.md")
        assert rd.status_code == 200
        assert rd.json() == {"path": "system.md", "content": "be terse"}

        # Save the creature's config back, then confirm it persisted.
        cfg = loaded.json()["config"]
        cfg["description"] = "updated"
        s = client.put(PREFIX + "/rt", json={"config": cfg, "prompts": {}})
        assert s.status_code == 200
        assert client.get(PREFIX + "/rt").json()["config"]["description"] == ("updated")

        # Delete with confirm → gone from the listing.
        assert client.delete(PREFIX + "/rt?confirm=true").status_code == 200
        assert "rt" not in {c["name"] for c in client.get(PREFIX).json()}

    def test_read_prompt_unsafe_path_400(self, _workspace):
        # A traversal path in the prompt slot is rejected with 400
        # unsafe_path, not a 404. (URL-encoded so Starlette doesn't
        # normalise the ``../`` away before the route sees it.)
        client = _client(creatures_mod.router)
        client.post(PREFIX, json={"name": "trav", "description": ""})
        r = client.get(PREFIX + "/trav/prompts/%2e%2e%2fescape.md")
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "unsafe_path"

    def test_write_prompt_unsafe_path_400(self, _workspace):
        client = _client(creatures_mod.router)
        client.post(PREFIX, json={"name": "trav2", "description": ""})
        r = client.put(
            PREFIX + "/trav2/prompts/%2e%2e%2fescape.md", json={"content": "x"}
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "unsafe_path"

    def test_save_creature_unsafe_prompt_path_400(self, _workspace):
        # save_creature with a traversal key in the prompts map → 400.
        client = _client(creatures_mod.router)
        client.post(PREFIX, json={"name": "sv", "description": ""})
        r = client.put(
            PREFIX + "/sv",
            json={"config": {"name": "sv"}, "prompts": {"../evil.md": "bad"}},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "unsafe_path"

    def test_load_creature_unsafe_name_400(self, _workspace):
        # A dot-prefixed creature name is rejected at load with 400
        # unsafe_path (the workspace forbids hidden / traversal names).
        r = _client(creatures_mod.router).get(PREFIX + "/.hidden")
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "unsafe_path"


# ── /manifest ──────────────────────────────────────────────────


class TestManifestRoute:
    def test_unknown_kind(self, _workspace):
        r = _client(manifest_mod.router).post(
            PREFIX + "/sync", json={"kind": "not-a-kind", "name": "foo"}
        )
        assert r.status_code == 400

    def test_not_found(self, _workspace):
        # ``tools`` is a known kind, so the handler runs; the missing
        # module surfaces as a 404 not_found (not a 400 kind rejection).
        r = _client(manifest_mod.router).post(
            PREFIX + "/sync", json={"kind": "tools", "name": "ghost"}
        )
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "not_found"

    def test_sync_appends_then_idempotent(self, _workspace):
        # Scaffold a tool, then sync it into kohaku.yaml — the first
        # sync adds the entry, the second is a no-op (idempotent).
        from kohakuterrarium.api.routes.catalog import modules as modules_mod

        modules_client = _client(modules_mod.router)
        assert (
            modules_client.post(
                PREFIX + "/tools", json={"name": "synctool"}
            ).status_code
            == 201
        )
        r1 = _client(manifest_mod.router).post(
            PREFIX + "/sync", json={"kind": "tools", "name": "synctool"}
        )
        assert r1.status_code == 200
        assert r1.json()["added"] is True
        assert r1.json()["entry"]["name"] == "synctool"
        # Second call → still ok but added is False.
        r2 = _client(manifest_mod.router).post(
            PREFIX + "/sync", json={"kind": "tools", "name": "synctool"}
        )
        assert r2.status_code == 200
        assert r2.json()["added"] is False

    def test_sync_invalid_name_400(self, _workspace):
        # A dot-prefixed module name trips the workspace's name
        # sanitiser → 400 invalid_name.
        r = _client(manifest_mod.router).post(
            PREFIX + "/sync", json={"kind": "tools", "name": ".bad"}
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_name"


# ── /schema ────────────────────────────────────────────────────


class TestSchemaRoute:
    def test_builtin_tools(self, _workspace):
        from kohakuterrarium.studio.catalog.introspect import builtin_schema

        r = _client(schema_mod.router).post(
            PREFIX, json={"kind": "tools", "name": "", "type": "builtin"}
        )
        assert r.status_code == 200
        # Builtin schema is pure introspection — route returns it verbatim.
        assert r.json() == builtin_schema("tools")

    def test_trigger_type(self, _workspace):
        from kohakuterrarium.studio.catalog.introspect import builtin_schema

        r = _client(schema_mod.router).post(
            PREFIX, json={"kind": "tools", "name": "x", "type": "trigger"}
        )
        assert r.status_code == 200
        # Trigger-as-tool entries fall back to the builtin tools schema.
        assert r.json() == builtin_schema("tools")

    def test_custom_no_module_field(self, _workspace):
        # A custom entry without a `module` field → empty params plus a
        # missing_module warning.
        r = _client(schema_mod.router).post(
            PREFIX, json={"kind": "tools", "type": "custom"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["params"] == []
        assert body["warnings"][0]["code"] == "missing_module"

    def test_custom_unresolvable_module(self, _workspace):
        # A custom entry whose `module` can't be resolved on disk → empty
        # params plus a module_not_found warning.
        r = _client(schema_mod.router).post(
            PREFIX,
            json={"kind": "tools", "type": "custom", "module": "ghost.mod"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["params"] == []
        assert body["warnings"][0]["code"] == "module_not_found"

    def test_unknown_type_returns_empty(self, _workspace):
        r = _client(schema_mod.router).post(
            PREFIX, json={"kind": "tools", "type": "garbage"}
        )
        assert r.status_code == 200
        assert r.json() == {"params": [], "warnings": []}

    def test_custom_plugin_with_sidecar_schema(self, _workspace, tmp_path):
        # A custom plugin module with a ``<stem>.schema.json`` sidecar →
        # the route resolves the source AND merges the sidecar params.
        import json

        pkg = tmp_path / "myplugins"
        pkg.mkdir()
        (pkg / "cost.py").write_text(
            "class CostPlugin:\n"
            "    def __init__(self, limit=100):\n"
            "        self.limit = limit\n"
        )
        (pkg / "cost.schema.json").write_text(
            json.dumps([{"name": "limit", "type": "int", "default": 100}])
        )
        r = _client(schema_mod.router).post(
            PREFIX,
            json={
                "kind": "plugins",
                "type": "custom",
                "module": "myplugins.cost",
                "class_name": "CostPlugin",
            },
        )
        assert r.status_code == 200
        body = r.json()
        # The sidecar-driven param surfaces.
        assert any(p["name"] == "limit" for p in body["params"])

    def test_custom_plugin_malformed_sidecar_ignored(self, _workspace, tmp_path):
        # A malformed sidecar JSON is silently ignored — the route still
        # returns the plain ``__init__``-signature schema, no crash.
        pkg = tmp_path / "myplugins"
        pkg.mkdir()
        (pkg / "bad.py").write_text(
            "class BadPlugin:\n"
            "    def __init__(self, ratio=0.5):\n"
            "        self.ratio = ratio\n"
        )
        (pkg / "bad.schema.json").write_text("not valid json {")
        r = _client(schema_mod.router).post(
            PREFIX,
            json={
                "kind": "plugins",
                "type": "custom",
                "module": "myplugins.bad",
                "class_name": "BadPlugin",
            },
        )
        assert r.status_code == 200
        # Falls back to the __init__ signature → ``ratio`` is still there.
        assert any(p["name"] == "ratio" for p in r.json()["params"])

    def test_custom_plugin_no_sidecar_uses_plain_signature(self, _workspace, tmp_path):
        # A custom plugin module with NO ``.schema.json`` sidecar →
        # the route still resolves params from the plain ``__init__``.
        pkg = tmp_path / "myplugins"
        pkg.mkdir()
        (pkg / "plain.py").write_text(
            "class PlainPlugin:\n"
            "    def __init__(self, window=10):\n"
            "        self.window = window\n"
        )
        # Deliberately no plain.schema.json next to it.
        r = _client(schema_mod.router).post(
            PREFIX,
            json={
                "kind": "plugins",
                "type": "custom",
                "module": "myplugins.plain",
                "class_name": "PlainPlugin",
            },
        )
        assert r.status_code == 200
        assert any(p["name"] == "window" for p in r.json()["params"])

    def test_package_type_with_module(self, _workspace, tmp_path):
        # ``type: package`` follows the same custom-resolution path; an
        # unresolvable module reference → module_not_found warning.
        r = _client(schema_mod.router).post(
            PREFIX,
            json={"kind": "tools", "type": "package", "module": "ghost.pkg"},
        )
        assert r.status_code == 200
        assert r.json()["warnings"][0]["code"] == "module_not_found"

    def test_load_plugin_sidecar_empty_module_returns_none(self, tmp_path):
        # The sidecar helper short-circuits on an empty module reference
        # (the documented "missing → None" contract) without touching
        # the filesystem.
        assert schema_mod._load_plugin_sidecar(tmp_path, "") is None
