"""Regression coverage for recursive pattern separators in GlobTool."""

import os

import pytest

from kohakuterrarium.builtins.tools.glob import GlobTool
from kohakuterrarium.modules.tool.base import ToolContext


@pytest.mark.parametrize("limit", [1, 10])
async def test_recursive_backslash_pattern_preserves_mtime_order_and_cap(
    tmp_path, limit
):
    sub = tmp_path / "src" / "nested"
    sub.mkdir(parents=True)
    older = tmp_path / "src" / "older.py"
    newer = sub / "newer.py"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    (tmp_path / "outside.py").write_text("outside", encoding="utf-8")
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    context = ToolContext(agent_name="glob-test", session=None, working_dir=tmp_path)
    tool = GlobTool()
    expected = await tool.execute(
        {"pattern": "src/**/*.py", "limit": limit}, context=context
    )
    actual = await tool.execute(
        {"pattern": r"src\**\*.py", "limit": limit}, context=context
    )
    assert actual.success and expected.success
    assert actual.output == expected.output
    assert actual.output.splitlines()[0] == str(newer.relative_to(tmp_path))
    assert "outside.py" not in actual.output
    if limit == 1:
        assert "older.py" not in actual.output
        assert "2 total, showing 1" in actual.output
    else:
        assert actual.output.splitlines()[1] == str(older.relative_to(tmp_path))
