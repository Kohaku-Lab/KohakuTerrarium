"""
Unit tests for the filesystem-manipulation tools: file_move, file_delete, mkdir.

These tools are structured replacements for ``bash mv``, ``bash rm`` and
``bash mkdir -p``. They apply the same path-boundary and read-state guards
as ``write`` / ``edit``, so these tests focus on:

- Happy paths (file + directory cases)
- Guard enforcement (read-before-write/delete, path boundary)
- Read-state bookkeeping on move/delete
"""

import os
from pathlib import Path

from kohakuterrarium.builtins.tool_catalog import (
    get_builtin_tool,
    list_builtin_tools,
)
from kohakuterrarium.builtins.tools.file_delete import FileDeleteTool
from kohakuterrarium.builtins.tools.file_move import FileMoveTool
from kohakuterrarium.builtins.tools.mkdir import MkdirTool
from kohakuterrarium.builtins.tools.read import ReadTool
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.utils.file_guard import FileReadState, PathBoundaryGuard

# =============================================================================
# Helpers
# =============================================================================


def _make_context(working_dir: Path) -> ToolContext:
    return ToolContext(
        agent_name="test_agent",
        session=None,
        working_dir=working_dir,
        file_read_state=FileReadState(),
        path_guard=PathBoundaryGuard(cwd=str(working_dir), mode="warn"),
    )


async def _read(target: Path, context: ToolContext) -> None:
    result = await ReadTool().execute({"path": str(target)}, context=context)
    assert result.success, f"Read failed: {result.error}"


# =============================================================================
# Registration
# =============================================================================


class TestRegistration:
    """The new tools must be available through the builtin catalog."""

    def test_all_registered(self):
        names = list_builtin_tools()
        assert "file_move" in names
        assert "file_delete" in names
        assert "mkdir" in names

    def test_get_builtin_returns_instances(self):
        assert get_builtin_tool("file_move") is not None
        assert get_builtin_tool("file_delete") is not None
        assert get_builtin_tool("mkdir") is not None


# =============================================================================
# file_move
# =============================================================================


class TestFileMove:
    async def test_rename_file(self, tmp_path: Path):
        src = tmp_path / "old.py"
        src.write_text("print('hi')\n")
        dst = tmp_path / "new.py"

        context = _make_context(tmp_path)
        tool = FileMoveTool()

        result = await tool.execute({"src": str(src), "dst": str(dst)}, context=context)
        assert result.success, f"file_move failed: {result.error}"
        assert not src.exists()
        assert dst.read_text() == "print('hi')\n"

    async def test_move_into_nested_parent_creates_dirs(self, tmp_path: Path):
        src = tmp_path / "file.txt"
        src.write_text("content")
        dst = tmp_path / "nested" / "deeper" / "file.txt"

        context = _make_context(tmp_path)
        tool = FileMoveTool()

        result = await tool.execute({"src": str(src), "dst": str(dst)}, context=context)
        assert result.success, f"file_move failed: {result.error}"
        assert dst.exists()
        assert dst.read_text() == "content"

    async def test_refuses_existing_dst_without_overwrite(self, tmp_path: Path):
        src = tmp_path / "src.txt"
        src.write_text("src")
        dst = tmp_path / "dst.txt"
        dst.write_text("dst")

        context = _make_context(tmp_path)
        tool = FileMoveTool()

        result = await tool.execute({"src": str(src), "dst": str(dst)}, context=context)
        assert not result.success
        assert "already exists" in result.error
        # Neither side should have changed
        assert src.read_text() == "src"
        assert dst.read_text() == "dst"

    async def test_overwrite_requires_read_of_dst(self, tmp_path: Path):
        """overwrite=true still enforces read-before-write on the destination."""
        src = tmp_path / "src.txt"
        src.write_text("src")
        dst = tmp_path / "dst.txt"
        dst.write_text("dst")

        context = _make_context(tmp_path)
        tool = FileMoveTool()

        result = await tool.execute(
            {"src": str(src), "dst": str(dst), "overwrite": True},
            context=context,
        )
        assert not result.success
        assert "has not been read yet" in result.error
        # No partial state: both files still as they were
        assert dst.read_text() == "dst"
        assert src.read_text() == "src"

    async def test_overwrite_succeeds_after_reading_dst(self, tmp_path: Path):
        src = tmp_path / "src.txt"
        src.write_text("src")
        dst = tmp_path / "dst.txt"
        dst.write_text("dst")

        context = _make_context(tmp_path)
        await _read(dst, context)

        tool = FileMoveTool()
        result = await tool.execute(
            {"src": str(src), "dst": str(dst), "overwrite": True},
            context=context,
        )
        assert result.success, f"file_move failed: {result.error}"
        assert dst.read_text() == "src"
        assert not src.exists()

    async def test_rejects_self_move(self, tmp_path: Path):
        src = tmp_path / "file.txt"
        src.write_text("content")

        context = _make_context(tmp_path)
        tool = FileMoveTool()

        result = await tool.execute({"src": str(src), "dst": str(src)}, context=context)
        assert not result.success
        assert "same path" in result.error
        assert src.read_text() == "content"

    async def test_rejects_missing_src(self, tmp_path: Path):
        context = _make_context(tmp_path)
        tool = FileMoveTool()

        result = await tool.execute(
            {"src": str(tmp_path / "missing"), "dst": str(tmp_path / "dst")},
            context=context,
        )
        assert not result.success
        assert "not found" in result.error

    async def test_rejects_missing_args(self, tmp_path: Path):
        context = _make_context(tmp_path)
        tool = FileMoveTool()

        r1 = await tool.execute({"dst": str(tmp_path / "d")}, context=context)
        assert not r1.success
        assert "src" in r1.error

        r2 = await tool.execute({"src": str(tmp_path / "s")}, context=context)
        assert not r2.success
        assert "dst" in r2.error

    async def test_source_alias(self, tmp_path: Path):
        """``source`` / ``destination`` aliases should work."""
        src = tmp_path / "a.txt"
        src.write_text("x")
        dst = tmp_path / "b.txt"

        context = _make_context(tmp_path)
        tool = FileMoveTool()

        result = await tool.execute(
            {"source": str(src), "destination": str(dst)},
            context=context,
        )
        assert result.success, f"file_move failed: {result.error}"
        assert dst.read_text() == "x"

    async def test_move_directory(self, tmp_path: Path):
        src = tmp_path / "pkg"
        src.mkdir()
        (src / "a.py").write_text("a")
        dst = tmp_path / "renamed"

        context = _make_context(tmp_path)
        tool = FileMoveTool()

        result = await tool.execute({"src": str(src), "dst": str(dst)}, context=context)
        assert result.success, f"file_move failed: {result.error}"
        assert not src.exists()
        assert (dst / "a.py").read_text() == "a"

    async def test_read_state_follows_file(self, tmp_path: Path):
        src = tmp_path / "old.py"
        src.write_text("hello\n")
        dst = tmp_path / "new.py"

        context = _make_context(tmp_path)
        await _read(src, context)
        src_record_before = context.file_read_state.get(str(src))
        assert src_record_before is not None

        tool = FileMoveTool()
        result = await tool.execute({"src": str(src), "dst": str(dst)}, context=context)
        assert result.success, f"file_move failed: {result.error}"

        # src record is gone, dst record exists
        assert context.file_read_state.get(str(src)) is None
        dst_record = context.file_read_state.get(str(dst))
        assert dst_record is not None
        assert dst_record.mtime_ns == src_record_before.mtime_ns

    async def test_read_state_cleared_for_directory_move(self, tmp_path: Path):
        src = tmp_path / "pkg"
        src.mkdir()
        inside = src / "a.py"
        inside.write_text("a")
        dst = tmp_path / "renamed"

        context = _make_context(tmp_path)
        await _read(inside, context)
        assert context.file_read_state.get(str(inside)) is not None

        tool = FileMoveTool()
        result = await tool.execute({"src": str(src), "dst": str(dst)}, context=context)
        assert result.success, f"file_move failed: {result.error}"

        # The old record under the old path must be dropped
        assert context.file_read_state.get(str(inside)) is None

    async def test_outside_cwd_blocked_first_attempt(self, tmp_path: Path):
        src = tmp_path / "file.txt"
        src.write_text("x")

        context = _make_context(tmp_path)
        tool = FileMoveTool()

        # Pick an arbitrary outside path (we will never actually move)
        outside = Path("/tmp") / "definitely_outside_cwd_xyz.txt"
        result = await tool.execute(
            {"src": str(src), "dst": str(outside)},
            context=context,
        )
        assert not result.success
        assert "outside the working directory" in result.error
        assert src.read_text() == "x"

    async def test_symlink_moved_as_link(self, tmp_path: Path):
        """Moving a symlink must rename the link, not follow to its target."""
        target = tmp_path / "real.txt"
        target.write_text("real content")
        link_src = tmp_path / "link_old"
        link_dst = tmp_path / "link_new"
        os.symlink(target, link_src)

        context = _make_context(tmp_path)
        tool = FileMoveTool()

        result = await tool.execute(
            {"src": str(link_src), "dst": str(link_dst)}, context=context
        )
        assert result.success, f"file_move failed: {result.error}"
        assert not link_src.exists()
        assert link_dst.is_symlink()
        # Target file is untouched
        assert target.read_text() == "real content"


# =============================================================================
# file_delete
# =============================================================================


class TestFileDelete:
    async def test_delete_file_requires_read(self, tmp_path: Path):
        target = tmp_path / "file.txt"
        target.write_text("content")

        context = _make_context(tmp_path)
        tool = FileDeleteTool()

        result = await tool.execute({"path": str(target)}, context=context)
        assert not result.success
        assert "has not been read yet" in result.error
        assert target.exists()

    async def test_delete_file_after_read(self, tmp_path: Path):
        target = tmp_path / "file.txt"
        target.write_text("content")

        context = _make_context(tmp_path)
        await _read(target, context)

        tool = FileDeleteTool()
        result = await tool.execute({"path": str(target)}, context=context)
        assert result.success, f"delete failed: {result.error}"
        assert not target.exists()
        # Read-state was cleared
        assert context.file_read_state.get(str(target)) is None

    async def test_delete_file_detects_stale_read(self, tmp_path: Path):
        target = tmp_path / "file.txt"
        target.write_text("original")

        context = _make_context(tmp_path)
        await _read(target, context)

        # External modification bumps the mtime
        target.write_text("modified")
        record = context.file_read_state.get(str(target))
        atime_ns = os.stat(target).st_atime_ns
        os.utime(target, ns=(atime_ns, record.mtime_ns + 1_000_000_000))

        tool = FileDeleteTool()
        result = await tool.execute({"path": str(target)}, context=context)
        assert not result.success
        assert "modified since last read" in result.error
        assert target.exists()

    async def test_delete_directory_requires_recursive(self, tmp_path: Path):
        target = tmp_path / "dir"
        target.mkdir()
        (target / "a.py").write_text("a")

        context = _make_context(tmp_path)
        tool = FileDeleteTool()

        result = await tool.execute({"path": str(target)}, context=context)
        assert not result.success
        assert "recursive=true" in result.error
        assert target.exists()

    async def test_delete_directory_recursive(self, tmp_path: Path):
        target = tmp_path / "dir"
        target.mkdir()
        inside = target / "a.py"
        inside.write_text("a")

        context = _make_context(tmp_path)
        await _read(inside, context)

        tool = FileDeleteTool()
        result = await tool.execute(
            {"path": str(target), "recursive": True}, context=context
        )
        assert result.success, f"delete failed: {result.error}"
        assert not target.exists()
        # Read state under the deleted tree was cleared
        assert context.file_read_state.get(str(inside)) is None

    async def test_rejects_missing_path_arg(self, tmp_path: Path):
        context = _make_context(tmp_path)
        tool = FileDeleteTool()

        result = await tool.execute({}, context=context)
        assert not result.success
        assert "No path" in result.error

    async def test_rejects_nonexistent_path(self, tmp_path: Path):
        context = _make_context(tmp_path)
        tool = FileDeleteTool()

        result = await tool.execute(
            {"path": str(tmp_path / "missing")}, context=context
        )
        assert not result.success
        assert "not found" in result.error

    async def test_delete_symlink_without_read(self, tmp_path: Path):
        """Symlinks are exempt from the read-before-delete guard."""
        target = tmp_path / "target.txt"
        target.write_text("content")
        link = tmp_path / "link.txt"
        os.symlink(target, link)

        context = _make_context(tmp_path)
        tool = FileDeleteTool()

        result = await tool.execute({"path": str(link)}, context=context)
        assert result.success, f"delete failed: {result.error}"
        assert not link.is_symlink()
        # The target must remain intact
        assert target.read_text() == "content"


# =============================================================================
# mkdir
# =============================================================================


class TestMkdir:
    async def test_creates_nested_dir(self, tmp_path: Path):
        target = tmp_path / "a" / "b" / "c"

        context = _make_context(tmp_path)
        tool = MkdirTool()

        result = await tool.execute({"path": str(target)}, context=context)
        assert result.success, f"mkdir failed: {result.error}"
        assert target.is_dir()

    async def test_existing_dir_succeeds(self, tmp_path: Path):
        target = tmp_path / "dir"
        target.mkdir()

        context = _make_context(tmp_path)
        tool = MkdirTool()

        result = await tool.execute({"path": str(target)}, context=context)
        assert result.success
        assert "already exists" in result.output

    async def test_error_if_exists_rejects_existing_dir(self, tmp_path: Path):
        target = tmp_path / "dir"
        target.mkdir()

        context = _make_context(tmp_path)
        tool = MkdirTool()

        result = await tool.execute(
            {"path": str(target), "error_if_exists": True}, context=context
        )
        assert not result.success
        assert "already exists" in result.error

    async def test_rejects_existing_file(self, tmp_path: Path):
        target = tmp_path / "file.txt"
        target.write_text("content")

        context = _make_context(tmp_path)
        tool = MkdirTool()

        result = await tool.execute({"path": str(target)}, context=context)
        assert not result.success
        assert "not a directory" in result.error
        assert target.is_file()

    async def test_parents_false_requires_existing_parent(self, tmp_path: Path):
        target = tmp_path / "missing_parent" / "child"

        context = _make_context(tmp_path)
        tool = MkdirTool()

        result = await tool.execute(
            {"path": str(target), "parents": False}, context=context
        )
        assert not result.success
        assert "Parent directory missing" in result.error
        assert not target.exists()

    async def test_rejects_missing_path_arg(self, tmp_path: Path):
        context = _make_context(tmp_path)
        tool = MkdirTool()

        result = await tool.execute({}, context=context)
        assert not result.success
        assert "No path" in result.error
