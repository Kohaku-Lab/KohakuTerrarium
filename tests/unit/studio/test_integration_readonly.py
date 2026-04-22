"""Phase 1 integration smoke — every read endpoint hit in sequence."""

from pathlib import Path


def _seed_workspace(tmp_workspace: Path):
    # One creature
    cdir = tmp_workspace / "creatures" / "alpha"
    cdir.mkdir(parents=True)
    (cdir / "config.yaml").write_text(
        'name: alpha\nversion: "1.0"\n'
        "description: integration fixture\n"
        "tools:\n  - {name: read, type: builtin}\n",
        encoding="utf-8",
    )
    (cdir / "prompts").mkdir()
    (cdir / "prompts" / "system.md").write_text(
        "# alpha\nYou are a test.",
        encoding="utf-8",
    )
    # One tool module
    tool_dir = tmp_workspace / "modules" / "tools"
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "my_tool.py").write_text(
        '"""TODO"""\n'
        "from typing import Any\n"
        "from kohakuterrarium.modules.tool.base import (\n"
        "    BaseTool, ExecutionMode, ToolResult,\n"
        ")\n\n"
        "class MyTool(BaseTool):\n"
        "    @property\n"
        "    def tool_name(self) -> str:\n"
        '        return "my_tool"\n'
        "    @property\n"
        "    def description(self) -> str:\n"
        '        return "test"\n'
        "    @property\n"
        "    def execution_mode(self) -> ExecutionMode:\n"
        "        return ExecutionMode.DIRECT\n"
        "    async def _execute(self, args, **kw):\n"
        '        return ToolResult(output="ok")\n',
        encoding="utf-8",
    )


def test_full_readonly_sweep(client, tmp_workspace: Path):
    _seed_workspace(tmp_workspace)

    # 1. Meta
    assert client.get("/api/studio/meta/health").status_code == 200
    assert client.get("/api/studio/meta/version").status_code == 200

    # 2. Workspace summary (already opened by fixture)
    resp = client.get("/api/studio/workspace")
    assert resp.status_code == 200
    summary = resp.json()
    assert any(c["name"] == "alpha" for c in summary["creatures"])

    # 3. Creatures
    resp = client.get("/api/studio/creatures")
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["alpha"]

    resp = client.get("/api/studio/creatures/alpha")
    assert resp.status_code == 200
    body = resp.json()
    assert body["config"]["name"] == "alpha"
    assert "prompts/system.md" in body["prompts"]

    # 4. Modules
    assert client.get("/api/studio/modules/tools").json()[0]["name"] == "my_tool"
    resp = client.get("/api/studio/modules/tools/my_tool")
    assert resp.status_code == 200
    assert resp.json()["form"]["tool_name"] == "my_tool"

    # 5. Catalog
    resp = client.get("/api/studio/catalog/tools")
    assert resp.status_code == 200
    assert len(resp.json()) > 0
    assert client.get("/api/studio/catalog/subagents").status_code == 200
    assert client.get("/api/studio/catalog/triggers").status_code == 200
    assert client.get("/api/studio/catalog/models").status_code == 200
    assert client.get("/api/studio/catalog/plugin_hooks").status_code == 200

    # 6. Packages
    assert client.get("/api/studio/packages").status_code == 200

    # 7. Templates
    assert client.get("/api/studio/templates").status_code == 200

    # 8. Validate
    assert (
        client.post(
            "/api/studio/validate/creature", json={"config": body["config"]}
        ).status_code
        == 200
    )
