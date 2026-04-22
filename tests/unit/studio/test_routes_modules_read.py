"""Read-only module route tests (list + detail)."""

from pathlib import Path

SAMPLE_TOOL_SOURCE = '''\
"""Sample tool."""
from typing import Any
from kohakuterrarium.modules.tool.base import (
    BaseTool, ExecutionMode, ToolResult,
)


class SampleTool(BaseTool):
    needs_context = False

    @property
    def tool_name(self) -> str:
        return "sample_tool"

    @property
    def description(self) -> str:
        return "A sample."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        return ToolResult(output="hi")
'''


def _write_tool(tmp_workspace: Path, name: str, source: str):
    p = tmp_workspace / "modules" / "tools" / f"{name}.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(source, encoding="utf-8")


def test_list_empty(client):
    resp = client.get("/api/studio/modules/tools")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_unknown_kind(client):
    resp = client.get("/api/studio/modules/nope")
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "unknown_kind"


def test_list_finds_tool(client, tmp_workspace: Path):
    _write_tool(tmp_workspace, "sample_tool", SAMPLE_TOOL_SOURCE)
    resp = client.get("/api/studio/modules/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "sample_tool"
    assert body[0]["kind"] == "tools"


def test_load_tool_parses_form(client, tmp_workspace: Path):
    _write_tool(tmp_workspace, "sample_tool", SAMPLE_TOOL_SOURCE)
    resp = client.get("/api/studio/modules/tools/sample_tool")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "tools"
    assert body["name"] == "sample_tool"
    assert body["mode"] == "simple"
    form = body["form"]
    assert form["class_name"] == "SampleTool"
    assert form["tool_name"] == "sample_tool"
    assert form["description"] == "A sample."
    assert form["execution_mode"] == "direct"
    # execute_body must contain the original return statement
    assert "ToolResult" in body["execute_body"]
    assert "raw_source" in body
    assert body["raw_source"] == SAMPLE_TOOL_SOURCE


def test_load_tool_missing_returns_404(client):
    resp = client.get("/api/studio/modules/tools/ghost")
    assert resp.status_code == 404


def test_load_subagent_without_config_call_is_raw(client, tmp_workspace: Path):
    # Non-SubAgentConfig file -> raw mode with a warning
    p = tmp_workspace / "modules" / "subagents" / "mine.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('SUBAGENT = "stub"\n', encoding="utf-8")
    resp = client.get("/api/studio/modules/subagents/mine")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "raw"
    codes = [w["code"] for w in body["warnings"]]
    assert "ast_roundtrip_unsafe" in codes
