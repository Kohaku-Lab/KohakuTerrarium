"""Unit tests for :mod:`kohakuterrarium.builtins.tools.json_read`."""

from kohakuterrarium.builtins.tools.json_read import JsonReadTool
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.utils.file_guard import PathBoundaryGuard


def _context(workspace):
    return ToolContext(
        agent_name="agent",
        session=None,
        working_dir=workspace,
        path_guard=PathBoundaryGuard(cwd=workspace, mode="block"),
    )


class TestJsonReadPathGuard:
    async def test_blocks_file_outside_working_directory(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.json"
        outside.write_text('{"secret": true}', encoding="utf-8")

        result = await JsonReadTool().execute(
            {"path": str(outside)}, context=_context(workspace)
        )

        assert result.success is False
        assert "Access denied" in result.error
        assert str(workspace) in result.error

    async def test_reads_file_inside_working_directory(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        inside = workspace / "data.json"
        inside.write_text('{"allowed": true}', encoding="utf-8")

        result = await JsonReadTool().execute(
            {"path": "data.json", "query": ".allowed"},
            context=_context(workspace),
        )

        assert result.success is True
        assert result.output == "True"
