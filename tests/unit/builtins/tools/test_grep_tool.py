"""Grep's directory ignore rules and explicit-file override."""

import pytest

from kohakuterrarium.builtins.tools.grep import GrepTool
from kohakuterrarium.modules.tool.base import ToolContext


@pytest.mark.parametrize("pattern", ["*.log", "**/*.log"])
async def test_gitignore_default_opt_out_and_single_file(tmp_path, pattern):
    (tmp_path / ".gitignore").write_text("*.log\n!keep.log\n", encoding="utf-8")
    (tmp_path / "drop.log").write_text("MATCH ignored\n", encoding="utf-8")
    (tmp_path / "keep.log").write_text("MATCH kept\n", encoding="utf-8")
    ctx = ToolContext(agent_name="agent", session=None, working_dir=tmp_path)
    tool = GrepTool()
    args = {"pattern": "MATCH", "glob": pattern}
    filtered = await tool.execute(args, context=ctx)
    assert filtered.success
    assert "keep.log:1: MATCH kept" in filtered.output
    assert "drop.log" not in filtered.output
    unfiltered = await tool.execute({**args, "gitignore": False}, context=ctx)
    assert unfiltered.success
    assert "drop.log:1: MATCH ignored" in unfiltered.output
    direct = await tool.execute({"pattern": "MATCH", "path": "drop.log"}, context=ctx)
    assert direct.success
    assert "MATCH ignored" in direct.output
