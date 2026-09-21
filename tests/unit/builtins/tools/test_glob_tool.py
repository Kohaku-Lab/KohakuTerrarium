"""Glob's ignore filtering preserves ordering and returned result limits."""

import os

import pytest

from kohakuterrarium.builtins.tools.glob import GlobTool
from kohakuterrarium.modules.tool.base import ToolContext


@pytest.mark.parametrize("pattern", ["*.log", "**/*.log"])
async def test_gitignore_filter_precedes_limit_and_mtime_sort(tmp_path, pattern):
    (tmp_path / ".gitignore").write_text("drop.log\n", encoding="utf-8")
    for index, name in enumerate(["old.log", "new.log", "drop.log"]):
        path = tmp_path / name
        path.write_text(name, encoding="utf-8")
        os.utime(path, (1000 + index, 1000 + index))
    ctx = ToolContext(agent_name="agent", session=None, working_dir=tmp_path)
    tool = GlobTool()
    args = {"pattern": pattern, "limit": 1}
    filtered = await tool.execute(args, context=ctx)
    assert filtered.success
    assert filtered.output.splitlines()[0] == "new.log"
    assert "drop.log" not in filtered.output
    unfiltered = await tool.execute({**args, "gitignore": False}, context=ctx)
    assert unfiltered.success
    assert unfiltered.output.splitlines()[0] == "drop.log"
