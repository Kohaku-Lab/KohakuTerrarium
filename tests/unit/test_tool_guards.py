"""
Unit tests for file safety guards.

Tests for:
- FileReadState: tracking which files the agent has read
- check_read_before_write: enforcing read-before-write
- PathBoundaryGuard: restricting file access outside cwd
- is_binary_file: detecting binary files
- Integration tests with read/write/edit tools
"""

import os
import time
from pathlib import Path

from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.utils.file_guard import (
    FileReadState,
    PathBoundaryGuard,
    check_read_before_write,
    is_binary_file,
)

# =============================================================================
# FileReadState Tests
# =============================================================================


class TestFileReadState:
    """Tests for FileReadState tracking."""

    def test_record_and_get(self, tmp_path: Path):
        """Recording a file read should be retrievable via get."""
        state = FileReadState()
        p = str(tmp_path / "test.py")
        state.record_read(p, mtime_ns=123456789, partial=False, timestamp=1.0)
        record = state.get(p)
        assert record is not None
        assert record.path == str(Path(p).resolve())
        assert record.mtime_ns == 123456789
        assert record.partial is False
        assert record.timestamp == 1.0

    def test_get_returns_none_for_unread(self, tmp_path: Path):
        """Getting a file that was never read should return None."""
        state = FileReadState()
        assert state.get(str(tmp_path / "never_read.txt")) is None

    def test_clear(self, tmp_path: Path):
        """Clearing should remove all recorded reads."""
        state = FileReadState()
        p = str(tmp_path / "file.txt")
        state.record_read(p, mtime_ns=100, partial=False, timestamp=1.0)
        assert state.get(p) is not None
        state.clear()
        assert state.get(p) is None


# =============================================================================
# check_read_before_write Tests
# =============================================================================


class TestCheckReadBeforeWrite:
    """Tests for the read-before-write guard."""

    def test_new_file_allowed(self, tmp_path: Path):
        """Writing a file that does not exist should always be allowed."""
        state = FileReadState()
        path = str(tmp_path / "new_file.txt")
        assert check_read_before_write(state, path) is None

    def test_existing_file_not_read_blocked(self, tmp_path: Path):
        """Writing an existing file that was never read should be blocked."""
        target = tmp_path / "existing.txt"
        target.write_text("content")
        state = FileReadState()
        msg = check_read_before_write(state, str(target))
        assert msg is not None
        assert "has not been read yet" in msg

    def test_existing_file_read_and_current_allowed(self, tmp_path: Path):
        """Writing an existing file that was read and not modified should be allowed."""
        target = tmp_path / "existing.txt"
        target.write_text("content")
        mtime_ns = os.stat(target).st_mtime_ns

        state = FileReadState()
        state.record_read(
            str(target), mtime_ns=mtime_ns, partial=False, timestamp=time.time()
        )

        assert check_read_before_write(state, str(target)) is None

    def test_existing_file_stale_blocked(self, tmp_path: Path):
        """Writing a file whose mtime changed since the read should be blocked."""
        target = tmp_path / "existing.txt"
        target.write_text("original")
        old_mtime_ns = os.stat(target).st_mtime_ns

        state = FileReadState()
        state.record_read(
            str(target), mtime_ns=old_mtime_ns, partial=False, timestamp=time.time()
        )

        # Modify the file externally and force a different mtime_ns
        target.write_text("modified externally")
        # Ensure mtime actually differs by setting it explicitly
        new_mtime_ns = old_mtime_ns + 1_000_000_000  # +1 second
        atime_ns = os.stat(target).st_atime_ns
        os.utime(target, ns=(atime_ns, new_mtime_ns))

        msg = check_read_before_write(state, str(target))
        assert msg is not None
        assert "modified since last read" in msg

    def test_none_state_blocks_existing_file(self, tmp_path: Path):
        """Passing None for file_read_state should block writes to existing files."""
        target = tmp_path / "existing.txt"
        target.write_text("content")
        msg = check_read_before_write(None, str(target))
        assert msg is not None
        assert "has not been read yet" in msg


# =============================================================================
# PathBoundaryGuard Tests
# =============================================================================


class TestPathBoundaryGuard:
    """Tests for path boundary enforcement."""

    def test_inside_cwd_allowed(self, tmp_path: Path):
        """Accessing files inside the working directory should always be allowed."""
        guard = PathBoundaryGuard(cwd=str(tmp_path), mode="warn")
        inside = str(tmp_path / "subdir" / "file.py")
        assert guard.check(inside) is None

    def test_outside_cwd_first_attempt_blocked(self, tmp_path: Path):
        """First access outside cwd in warn mode should be blocked with a warning."""
        guard = PathBoundaryGuard(cwd=str(tmp_path), mode="warn")
        outside = "/etc/passwd"
        msg = guard.check(outside)
        assert msg is not None
        assert "outside the working directory" in msg
        assert "retry" in msg.lower()

    def test_outside_cwd_retry_allowed(self, tmp_path: Path):
        """Second access to the same outside path in warn mode should be allowed."""
        guard = PathBoundaryGuard(cwd=str(tmp_path), mode="warn")
        outside = "/etc/passwd"
        # First attempt: blocked
        assert guard.check(outside) is not None
        # Retry: allowed
        assert guard.check(outside) is None

    def test_outside_cwd_block_mode_always_blocked(self, tmp_path: Path):
        """In block mode, outside access should always be denied, even on retry."""
        guard = PathBoundaryGuard(cwd=str(tmp_path), mode="block")
        outside = "/etc/passwd"
        msg1 = guard.check(outside)
        assert msg1 is not None
        assert "Access denied" in msg1
        # Retry is also blocked
        msg2 = guard.check(outside)
        assert msg2 is not None
        assert "Access denied" in msg2

    def test_off_mode_always_allowed(self, tmp_path: Path):
        """In off mode, all paths should be allowed."""
        guard = PathBoundaryGuard(cwd=str(tmp_path), mode="off")
        assert guard.check("/etc/passwd") is None
        assert guard.check("/root/.ssh/id_rsa") is None
        assert guard.check(str(tmp_path / "inside.txt")) is None


# =============================================================================
# is_binary_file Tests
# =============================================================================


class TestIsBinaryFile:
    """Tests for binary file detection."""

    def test_known_binary_extension(self, tmp_path: Path):
        """Files with known binary extensions should be detected."""
        png_file = tmp_path / "image.png"
        png_file.write_bytes(b"\x89PNG\r\n")
        assert is_binary_file(str(png_file)) is True

    def test_text_file_not_binary(self, tmp_path: Path):
        """Regular text files should not be detected as binary."""
        text_file = tmp_path / "code.py"
        text_file.write_text("def hello():\n    print('world')\n")
        assert is_binary_file(str(text_file)) is False

    def test_file_with_null_bytes_detected(self, tmp_path: Path):
        """Files with significant null byte content should be detected as binary."""
        binary_file = tmp_path / "data.dat"
        # Create content where >10% of bytes are non-printable
        content = b"\x00" * 20 + b"hello" * 5
        binary_file.write_bytes(content)
        assert is_binary_file(str(binary_file)) is True

    def test_empty_file_not_binary(self, tmp_path: Path):
        """Empty files should not be considered binary."""
        empty = tmp_path / "empty.txt"
        empty.write_bytes(b"")
        assert is_binary_file(str(empty)) is False

    def test_nonexistent_file_not_binary(self, tmp_path: Path):
        """Non-existent files should not be considered binary (let caller handle)."""
        assert is_binary_file(str(tmp_path / "does_not_exist.txt")) is False


# =============================================================================
# Helper: build a minimal ToolContext with guards
# =============================================================================


def _make_context(working_dir: Path) -> ToolContext:
    """Build a ToolContext with FileReadState and PathBoundaryGuard."""
    return ToolContext(
        agent_name="test_agent",
        session=None,
        working_dir=working_dir,
        file_read_state=FileReadState(),
        path_guard=PathBoundaryGuard(cwd=str(working_dir), mode="warn"),
    )


# =============================================================================
# Integration: read tool records state
# =============================================================================


class TestReadToolIntegration:
    """Tests that the read tool records file state into the context."""

    async def test_read_records_file_state(self, tmp_path: Path):
        """After reading a file via ReadTool, the state should be recorded."""
        from kohakuterrarium.builtins.tools.read import ReadTool

        target = tmp_path / "sample.py"
        target.write_text("line1\nline2\nline3\n")

        context = _make_context(tmp_path)
        tool = ReadTool()

        result = await tool.execute({"path": str(target)}, context=context)
        assert result.success, f"Read failed: {result.error}"

        record = context.file_read_state.get(str(target))
        assert record is not None
        assert record.mtime_ns == os.stat(target).st_mtime_ns
        assert record.partial is False


# =============================================================================
# Integration: write tool checks state
# =============================================================================


class TestWriteToolIntegration:
    """Tests that the write tool enforces read-before-write."""

    async def test_write_blocks_without_read(self, tmp_path: Path):
        """Writing an existing file without reading first should fail."""
        from kohakuterrarium.builtins.tools.write import WriteTool

        target = tmp_path / "guarded.txt"
        target.write_text("original content")

        context = _make_context(tmp_path)
        tool = WriteTool()

        result = await tool.execute(
            {"path": str(target), "content": "overwritten"},
            context=context,
        )
        assert not result.success
        assert "has not been read yet" in result.error

    async def test_write_succeeds_after_read(self, tmp_path: Path):
        """Writing after reading (with current mtime) should succeed."""
        from kohakuterrarium.builtins.tools.read import ReadTool
        from kohakuterrarium.builtins.tools.write import WriteTool

        target = tmp_path / "guarded.txt"
        target.write_text("original content")

        context = _make_context(tmp_path)
        read_tool = ReadTool()
        write_tool = WriteTool()

        # Read first
        read_result = await read_tool.execute({"path": str(target)}, context=context)
        assert read_result.success, f"Read failed: {read_result.error}"

        # Write should now succeed
        write_result = await write_tool.execute(
            {"path": str(target), "content": "new content"},
            context=context,
        )
        assert write_result.success, f"Write failed: {write_result.error}"
        assert target.read_text() == "new content"


# =============================================================================
# Integration: edit search/replace mode
# =============================================================================


class TestEditSearchReplaceIntegration:
    """Tests for the edit tool's search/replace mode with guard integration."""

    async def _read_then_edit(
        self,
        target: Path,
        context: ToolContext,
        edit_args: dict,
    ):
        """Helper: read a file then apply an edit, returning the edit result."""
        from kohakuterrarium.builtins.tools.edit import EditTool
        from kohakuterrarium.builtins.tools.read import ReadTool

        read_tool = ReadTool()
        edit_tool = EditTool()

        read_result = await read_tool.execute({"path": str(target)}, context=context)
        assert read_result.success, f"Read failed: {read_result.error}"

        edit_args["path"] = str(target)
        return await edit_tool.execute(edit_args, context=context)

    async def test_search_replace_single_match(self, tmp_path: Path):
        """Replacing a unique string should succeed."""
        target = tmp_path / "code.py"
        target.write_text("def hello():\n    return 'world'\n")

        context = _make_context(tmp_path)
        result = await self._read_then_edit(
            target,
            context,
            {"old": "return 'world'", "new": "return 'universe'"},
        )
        assert result.success, f"Edit failed: {result.error}"
        assert "universe" in target.read_text()
        assert "world" not in target.read_text()

    async def test_search_replace_multiple_match_no_replace_all_errors(
        self, tmp_path: Path
    ):
        """Multiple matches without replace_all should return an error."""
        target = tmp_path / "code.py"
        target.write_text("foo = 1\nfoo = 2\nfoo = 3\n")

        context = _make_context(tmp_path)
        result = await self._read_then_edit(
            target,
            context,
            {"old": "foo", "new": "bar"},
        )
        assert not result.success
        assert "occurrences" in result.error

    async def test_search_replace_replace_all(self, tmp_path: Path):
        """replace_all=True should replace every occurrence."""
        target = tmp_path / "code.py"
        target.write_text("foo = 1\nfoo = 2\nfoo = 3\n")

        context = _make_context(tmp_path)
        result = await self._read_then_edit(
            target,
            context,
            {"old": "foo", "new": "bar", "replace_all": True},
        )
        assert result.success, f"Edit failed: {result.error}"
        content = target.read_text()
        assert content.count("bar") == 3
        assert "foo" not in content

    async def test_search_replace_not_found_errors(self, tmp_path: Path):
        """Searching for a string not in the file should return an error."""
        target = tmp_path / "code.py"
        target.write_text("hello world\n")

        context = _make_context(tmp_path)
        result = await self._read_then_edit(
            target,
            context,
            {"old": "nonexistent_string_xyz", "new": "anything"},
        )
        assert not result.success
        assert "not found" in result.error

    async def test_search_replace_blocks_without_read(self, tmp_path: Path):
        """Editing an existing file without reading first should fail."""
        from kohakuterrarium.builtins.tools.edit import EditTool

        target = tmp_path / "code.py"
        target.write_text("hello world\n")

        context = _make_context(tmp_path)
        edit_tool = EditTool()

        result = await edit_tool.execute(
            {"path": str(target), "old": "hello", "new": "goodbye"},
            context=context,
        )
        assert not result.success
        assert "has not been read yet" in result.error
