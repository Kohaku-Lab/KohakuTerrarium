"""Unit tests for :mod:`kohakuterrarium.builtins.tools.json_write`."""

import json

from kohakuterrarium.builtins.tools.json_write import JsonWriteTool
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.utils.file_guard import PathBoundaryGuard


def _context(workspace):
    return ToolContext(
        agent_name="agent",
        session=None,
        working_dir=workspace,
        path_guard=PathBoundaryGuard(cwd=workspace, mode="block"),
    )


class TestJsonWritePathGuard:
    async def test_blocks_new_file_outside_working_directory(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.json"

        result = await JsonWriteTool().execute(
            {"path": str(outside), "value": '{"secret": true}'},
            context=_context(workspace),
        )

        assert result.success is False
        assert "Access denied" in result.error
        assert str(workspace) in result.error
        assert outside.exists() is False

    async def test_updates_file_inside_working_directory(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        inside = workspace / "data.json"
        inside.write_text('{"allowed": false}', encoding="utf-8")

        result = await JsonWriteTool().execute(
            {"path": "data.json", "query": ".allowed", "value": "true"},
            context=_context(workspace),
        )

        assert result.success is True
        assert json.loads(inside.read_text(encoding="utf-8")) == {"allowed": True}
