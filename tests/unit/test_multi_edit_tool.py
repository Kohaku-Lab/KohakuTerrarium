import os
from pathlib import Path

from kohakuterrarium.builtins.tools.multi_edit import MultiEditTool
from kohakuterrarium.builtins.tools.read import ReadTool
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.utils.file_guard import FileReadState, PathBoundaryGuard


def _make_context(working_dir: Path) -> ToolContext:
    return ToolContext(
        agent_name="test_agent",
        session=None,
        working_dir=working_dir,
        file_read_state=FileReadState(),
        path_guard=PathBoundaryGuard(cwd=str(working_dir), mode="warn"),
    )


async def _read_then_multi_edit(
    target: Path,
    context: ToolContext,
    args: dict,
):
    read_tool = ReadTool()
    multi_edit_tool = MultiEditTool()
    read_result = await read_tool.execute({"path": str(target)}, context=context)
    assert read_result.success, f"Read failed: {read_result.error}"
    payload = dict(args)
    payload["path"] = str(target)
    return await multi_edit_tool.execute(payload, context=context)


class TestMultiEditValidation:
    async def test_blocks_without_read(self, tmp_path: Path):
        target = tmp_path / "sample.py"
        target.write_text("hello\n")
        tool = MultiEditTool()
        context = _make_context(tmp_path)

        result = await tool.execute(
            {
                "path": str(target),
                "edits": [{"old": "hello", "new": "goodbye"}],
            },
            context=context,
        )
        assert not result.success
        assert "has not been read yet" in result.error

    async def test_rejects_empty_edits(self, tmp_path: Path):
        target = tmp_path / "sample.py"
        target.write_text("hello\n")
        context = _make_context(tmp_path)

        result = await _read_then_multi_edit(target, context, {"edits": []})
        assert not result.success
        assert "non-empty array" in result.error

    async def test_rejects_empty_old(self, tmp_path: Path):
        target = tmp_path / "sample.py"
        target.write_text("hello\n")
        context = _make_context(tmp_path)

        result = await _read_then_multi_edit(
            target,
            context,
            {"edits": [{"old": "", "new": "goodbye"}]},
        )
        assert not result.success
        assert "must not be empty" in result.error

    async def test_rejects_strict_and_best_effort_together(self, tmp_path: Path):
        target = tmp_path / "sample.py"
        target.write_text("hello\n")
        context = _make_context(tmp_path)

        result = await _read_then_multi_edit(
            target,
            context,
            {
                "strict": True,
                "best_effort": True,
                "edits": [{"old": "hello", "new": "goodbye"}],
            },
        )
        assert not result.success
        assert "cannot be used together" in result.error


class TestMultiEditStrictMode:
    async def test_applies_multiple_edits_atomically(self, tmp_path: Path):
        target = tmp_path / "code.py"
        target.write_text("class OldName:\n    pass\n\nOldName()\n")
        context = _make_context(tmp_path)

        result = await _read_then_multi_edit(
            target,
            context,
            {
                "edits": [
                    {"old": "class OldName", "new": "class NewName"},
                    {"old": "OldName()", "new": "NewName()"},
                ]
            },
        )

        assert result.success, result.error
        content = target.read_text()
        assert "NewName" in content
        assert "OldName" not in content
        assert "mode: strict" in result.output
        assert "edit[0]: ok: 1 replacement" in result.output
        assert "--- a/" in result.output
        assert "+class NewName:" in result.output

    async def test_failure_keeps_file_unchanged(self, tmp_path: Path):
        target = tmp_path / "code.py"
        original = "alpha\nbeta\ngamma\n"
        target.write_text(original)
        context = _make_context(tmp_path)

        result = await _read_then_multi_edit(
            target,
            context,
            {
                "edits": [
                    {"old": "alpha", "new": "ALPHA"},
                    {"old": "missing", "new": "x"},
                ]
            },
        )

        assert not result.success
        assert "strict mode" in result.error
        assert target.read_text() == original
        assert "No changes made" in result.output
        assert "edit[1]: error: old not found in file after prior edits" in result.output

    async def test_old_equals_new_is_success(self, tmp_path: Path):
        target = tmp_path / "code.py"
        target.write_text("hello\n")
        context = _make_context(tmp_path)

        result = await _read_then_multi_edit(
            target,
            context,
            {"edits": [{"old": "hello", "new": "hello"}]},
        )

        assert result.success, result.error
        assert target.read_text() == "hello\n"
        assert "ok: no change (old equals new)" in result.output


class TestMultiEditPartialMode:
    async def test_writes_successes_before_first_failure(self, tmp_path: Path):
        target = tmp_path / "code.py"
        target.write_text("alpha\nbeta\ngamma\n")
        context = _make_context(tmp_path)

        result = await _read_then_multi_edit(
            target,
            context,
            {
                "strict": False,
                "edits": [
                    {"old": "alpha", "new": "ALPHA"},
                    {"old": "missing", "new": "x"},
                    {"old": "gamma", "new": "GAMMA"},
                ],
            },
        )

        assert not result.success
        assert "completed with 1 failed edit" in result.error
        assert target.read_text() == "ALPHA\nbeta\ngamma\n"
        assert "mode: partial" in result.output
        assert "edit[2]: skipped" in result.output
        assert "+ALPHA" in result.output
        assert "+GAMMA" not in result.output


class TestMultiEditBestEffortMode:
    async def test_continues_past_failures(self, tmp_path: Path):
        target = tmp_path / "code.py"
        target.write_text("alpha\nbeta\ngamma\n")
        context = _make_context(tmp_path)

        result = await _read_then_multi_edit(
            target,
            context,
            {
                "strict": False,
                "best_effort": True,
                "edits": [
                    {"old": "alpha", "new": "ALPHA"},
                    {"old": "missing", "new": "x"},
                    {"old": "gamma", "new": "GAMMA"},
                ],
            },
        )

        assert not result.success
        assert target.read_text() == "ALPHA\nbeta\nGAMMA\n"
        assert "mode: best_effort" in result.output
        assert "edit[1]: error: old not found in file after prior edits" in result.output
        assert "edit[2]: ok: 1 replacement" in result.output
        assert "+GAMMA" in result.output

    async def test_replace_all_per_edit(self, tmp_path: Path):
        target = tmp_path / "code.py"
        target.write_text("foo\nfoo\nbar\n")
        context = _make_context(tmp_path)

        result = await _read_then_multi_edit(
            target,
            context,
            {
                "strict": False,
                "best_effort": True,
                "edits": [
                    {"old": "foo", "new": "baz", "replace_all": True},
                    {"old": "bar", "new": "qux"},
                ],
            },
        )

        assert result.success, result.error
        assert target.read_text() == "baz\nbaz\nqux\n"
        assert "edit[0]: ok: 2 replacements" in result.output
        assert "edit[1]: ok: 1 replacement" in result.output


class TestMultiEditReadState:
    async def test_updates_read_state_after_write(self, tmp_path: Path):
        target = tmp_path / "code.py"
        target.write_text("hello\n")
        context = _make_context(tmp_path)

        before_mtime = os.stat(target).st_mtime_ns
        result = await _read_then_multi_edit(
            target,
            context,
            {"edits": [{"old": "hello", "new": "goodbye"}]},
        )
        assert result.success, result.error

        record = context.file_read_state.get(str(target))
        assert record is not None
        assert record.mtime_ns == os.stat(target).st_mtime_ns
        assert record.mtime_ns >= before_mtime

    async def test_stale_read_state_blocks_edit(self, tmp_path: Path):
        target = tmp_path / "code.py"
        target.write_text("hello\n")
        context = _make_context(tmp_path)
        read_tool = ReadTool()
        read_result = await read_tool.execute({"path": str(target)}, context=context)
        assert read_result.success

        target.write_text("changed externally\n")
        old_mtime = os.stat(target).st_mtime_ns
        os.utime(target, ns=(old_mtime, old_mtime + 1_000_000_000))

        tool = MultiEditTool()
        result = await tool.execute(
            {"path": str(target), "edits": [{"old": "changed", "new": "updated"}]},
            context=context,
        )
        assert not result.success
        assert "modified since last read" in result.error
