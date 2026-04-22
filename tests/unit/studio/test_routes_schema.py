"""Integration tests for POST /api/studio/module_schema."""

from pathlib import Path

CUSTOM_TOOL_SOURCE = '''\
"""Custom tool fixture."""

class MyTool:
    def __init__(self, keywords_file: str | None = None, drop: float = 0.5):
        pass
'''


def test_builtin_tool_schema(client):
    resp = client.post(
        "/api/studio/module_schema",
        json={"kind": "tools", "name": "read", "type": "builtin"},
    )
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()["params"]]
    assert "timeout" in names
    assert "notify_controller_on_background_complete" in names


def test_builtin_subagent_schema(client):
    resp = client.post(
        "/api/studio/module_schema",
        json={"kind": "subagents", "name": "explore", "type": "builtin"},
    )
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()["params"]]
    assert "max_turns" in names
    assert "interactive" in names


def test_trigger_type_returns_tools_schema(client):
    resp = client.post(
        "/api/studio/module_schema",
        json={"kind": "tools", "name": "add_timer", "type": "trigger"},
    )
    # Trigger-as-tool entries reuse the tools schema.
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()["params"]]
    assert "timeout" in names


def test_custom_tool_schema(client, tmp_workspace: Path):
    custom_dir = tmp_workspace / "custom"
    custom_dir.mkdir()
    (custom_dir / "my_tool.py").write_text(CUSTOM_TOOL_SOURCE, encoding="utf-8")
    resp = client.post(
        "/api/studio/module_schema",
        json={
            "kind": "tools",
            "name": "my_tool",
            "type": "custom",
            "module": "./custom/my_tool.py",
            "class_name": "MyTool",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    names = [p["name"] for p in body["params"]]
    assert names == ["keywords_file", "drop"]
    assert body["warnings"] == []


def test_custom_missing_module_warns(client):
    resp = client.post(
        "/api/studio/module_schema",
        json={
            "kind": "tools",
            "name": "bad",
            "type": "custom",
            # no module path
        },
    )
    assert resp.status_code == 200
    codes = [w["code"] for w in resp.json()["warnings"]]
    assert "missing_module" in codes


def test_custom_nonexistent_module_warns(client):
    resp = client.post(
        "/api/studio/module_schema",
        json={
            "kind": "tools",
            "name": "bad",
            "type": "custom",
            "module": "./does_not_exist.py",
            "class_name": "X",
        },
    )
    assert resp.status_code == 200
    codes = [w["code"] for w in resp.json()["warnings"]]
    assert "module_not_found" in codes
