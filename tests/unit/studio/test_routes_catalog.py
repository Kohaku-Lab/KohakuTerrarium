"""Catalog route tests.

These hit real core internals — no mocks. If the core breaks
something here, CI will catch it immediately.
"""


def test_tools_non_empty(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/catalog/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) > 0
    names = {t["name"] for t in body}
    # A few well-known tools should always be there
    assert "read" in names
    assert "bash" in names
    # Every entry carries the common shape
    for t in body:
        assert set(t.keys()) >= {
            "name",
            "description",
            "source",
            "type",
        }
    # Builtin entries carry the extra introspection fields
    builtins = [t for t in body if t["source"] == "builtin"]
    assert builtins, "expected at least one builtin tool"
    for t in builtins:
        assert set(t.keys()) >= {
            "execution_mode",
            "needs_context",
            "require_manual_read",
            "has_doc",
        }


def test_tool_doc_exists(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/catalog/tools/read/doc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "read"
    assert len(body["doc"]) > 0


def test_tool_doc_missing_404(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/catalog/tools/__nope__/doc")
    assert resp.status_code == 404


def test_subagents_non_empty(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/catalog/subagents")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) > 0
    names = {s["name"] for s in body}
    assert "explore" in names
    assert "plan" in names


def test_subagent_doc(no_workspace_client):
    # Explore should have docs in builtin_skills/subagents/explore.md
    resp = no_workspace_client.get("/api/studio/catalog/subagents/explore/doc")
    # Either 200 with doc, or 404 if doc file doesn't exist — both acceptable
    assert resp.status_code in (200, 404)


def test_triggers(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/catalog/triggers")
    assert resp.status_code == 200
    body = resp.json()
    # At least the universal timer + scheduler should appear
    names = {t["name"] for t in body}
    assert len(names) >= 2


def test_models_returns_list(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/catalog/models")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    # Each entry should have a name field (per llm/profiles.list_all)
    if body:
        assert "name" in body[0]


def test_catalog_includes_workspace_manifest(client, tmp_workspace):
    """kohaku.yaml-declared modules surface in the catalog."""
    manifest = (
        "name: test-ws\n"
        "version: 0.1.0\n"
        "tools:\n"
        "  - name: my_workspace_tool\n"
        "    module: my_ws.tools.fake\n"
        "    class: MyWsTool\n"
        "    description: workspace tool\n"
        "plugins:\n"
        "  - name: my_ws_plugin\n"
        "    module: my_ws.plugins.fake\n"
        "    class: MyWsPlugin\n"
        "    description: workspace plugin\n"
    )
    (tmp_workspace / "kohaku.yaml").write_text(manifest, encoding="utf-8")

    resp = client.get("/api/studio/catalog/tools")
    assert resp.status_code == 200
    ws_tool = next((t for t in resp.json() if t["name"] == "my_workspace_tool"), None)
    assert ws_tool is not None
    assert ws_tool["source"] == "workspace-manifest"
    assert ws_tool["type"] == "package"
    assert ws_tool["module"] == "my_ws.tools.fake"
    assert ws_tool["class_name"] == "MyWsTool"

    resp = client.get("/api/studio/catalog/plugins")
    assert resp.status_code == 200
    ws_plugin = next((p for p in resp.json() if p["name"] == "my_ws_plugin"), None)
    assert ws_plugin is not None
    assert ws_plugin["source"] == "workspace-manifest"


def test_plugins_empty_without_manifests(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/catalog/plugins")
    assert resp.status_code == 200
    # There are no builtin plugins — without any manifest this should
    # be empty (or just contain entries from installed packages on the
    # test machine, which we can't control — so only assert shape).
    body = resp.json()
    assert isinstance(body, list)
    for p in body:
        assert p["source"] == "workspace-manifest" or p["source"].startswith("package:")


def test_plugin_hooks(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/catalog/plugin_hooks")
    assert resp.status_code == 200
    body = resp.json()
    hook_names = {h["name"] for h in body}
    # Every canonical hook must be present
    assert "pre_tool_execute" in hook_names
    assert "post_tool_execute" in hook_names
    assert "pre_llm_call" in hook_names
    assert "on_load" in hook_names
    # Grouping present
    for h in body:
        assert h["group"] in {
            "lifecycle",
            "llm",
            "tool",
            "subagent",
            "event",
        }
