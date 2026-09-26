"""Unified diff edits preserve coordinates and reject malformed patches."""

from difflib import unified_diff

import pytest

from kohakuterrarium.builtins.tools.read import ReadTool
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.utils.file_guard import FileReadState
from kohakuterrarium.builtins.tools.edit import (
    EditTool,
    apply_hunks,
    parse_unified_diff,
)


def patch(old, new, context=0):
    return "".join(
        unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/f",
            tofile="b/f",
            n=context,
        )
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ("a\nb\nc\n", "NEW\na\nb\nc\n"),
        ("a\nb\nc\n", "a\nNEW\nb\nc\n"),
        ("a\nb\nc\n", "a\nb\nc\nNEW\n"),
        ("", "NEW\n"),
        ("a\n", ""),
        ("--old\nkeep\n", "++new\nkeep\n"),
        ("a\nb\nc\nd\ne\nf\n", "A\nb\nc\nd\ne\nF\n"),
        ("a\n\nb\n", "a\n\nB\n"),
    ],
)
@pytest.mark.parametrize("context", [0, 3])
def test_standard_diff_exact_result(old, new, context):
    assert apply_hunks(old, parse_unified_diff(patch(old, new, context))) == new


@pytest.mark.parametrize(
    "diff",
    [
        "@@ -0,1 +1,1 @@\n-a\n+b\n",
        "@@ -9,0 +10,1 @@\n+x\n",
        "@@ -1,2 +1,1 @@\n-a\n+b\n",
        "@@ -1,1 +1,1 @@\n-a\n+b\n+c\n",
        "@@ -1,1 +1,1 @@\n a\n@@ -1,1 +1,1 @@\n a\n",
        "@@ -1,1 +1,1 @@\n-a\n+b\n--- a/other\n+++ b/other\n@@ -1 +1 @@\n-a\n+c\n",
    ],
)
async def test_invalid_diff_does_not_write(tmp_path, diff):
    context = ToolContext(
        agent_name="edit-test",
        session=None,
        working_dir=tmp_path,
        file_read_state=FileReadState(),
    )
    target = tmp_path / "file.txt"
    target.write_bytes(b"a\n")
    assert (
        await ReadTool().execute({"path": str(target)}, context=context)
    ).error is None
    result = await EditTool().execute(
        {"path": str(target), "diff": diff}, context=context
    )
    assert result.error
    assert "not been read" not in result.error
    assert target.read_bytes() == b"a\n"


def test_no_newline_markers_preserve_exact_text():
    diff = "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n"
    assert apply_hunks("old", parse_unified_diff(diff)) == "new"
    diff = "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n"
    assert apply_hunks("old", parse_unified_diff(diff)) == "new\n"


async def test_edit_writes_diff_then_search_replace(tmp_path):
    context = ToolContext(
        agent_name="edit-test",
        session=None,
        working_dir=tmp_path,
        file_read_state=FileReadState(),
    )
    target = tmp_path / "file.txt"
    target.write_bytes(b"a\nb\n")
    assert (
        await ReadTool().execute({"path": str(target)}, context=context)
    ).error is None
    tool = EditTool()
    result = await tool.execute(
        {"path": str(target), "diff": patch("a\nb\n", "a\nNEW\nb\n")}, context=context
    )
    assert result.error is None
    assert target.read_text() == "a\nNEW\nb\n"
    result = await tool.execute(
        {"path": str(target), "old": "NEW", "new": "DONE"}, context=context
    )
    assert result.error is None
    assert target.read_text() == "a\nDONE\nb\n"
