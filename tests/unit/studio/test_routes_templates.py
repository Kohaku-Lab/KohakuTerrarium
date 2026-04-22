"""Templates route tests."""


def test_list_templates(no_workspace_client):
    resp = no_workspace_client.get("/api/studio/templates")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0
    # Shape
    for t in body:
        assert set(t.keys()) >= {"id", "kind", "label", "template"}


def test_render_known_template(no_workspace_client):
    resp = no_workspace_client.post(
        "/api/studio/templates/render",
        json={
            "id": "creature-minimal",
            "context": {"name": "alpha", "base_config": None, "description": ""},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "creature-minimal"
    assert "name: alpha" in body["source"]


def test_render_unknown_template_404(no_workspace_client):
    resp = no_workspace_client.post(
        "/api/studio/templates/render",
        json={
            "id": "__nope__",
            "context": {},
        },
    )
    assert resp.status_code == 404


def test_render_tool_template(no_workspace_client):
    resp = no_workspace_client.post(
        "/api/studio/templates/render",
        json={
            "id": "tool-minimal",
            "context": {
                "name": "my_tool",
                "class_name": "MyTool",
                "tool_name": "my_tool",
                "description": "does a thing",
                "execution_mode": "direct",
                "needs_context": False,
                "execute_body": '        return ToolResult(output="hi")',
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    source = body["source"]
    assert "class MyTool(BaseTool)" in source
    assert '"my_tool"' in source
    assert "ExecutionMode.DIRECT" in source
    # Syntax check
    compile(source, "<rendered>", "exec")
