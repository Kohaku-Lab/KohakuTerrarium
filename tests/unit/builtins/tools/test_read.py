"""Unit tests for :mod:`kohakuterrarium.builtins.tools.read`."""

from kohakuterrarium.builtins.tools.read import MAX_DEFAULT_LINES, ReadTool
from kohakuterrarium.modules.tool.base import ToolContext


def _ctx(tmp_path):
    return ToolContext(agent_name="agent", session=None, working_dir=tmp_path)


class TestReadDefaultLineGuard:
    async def test_huge_file_capped_at_default_lines_with_navigation(self, tmp_path):
        path = tmp_path / "big.txt"
        path.write_text(
            "\n".join(f"line {i}" for i in range(MAX_DEFAULT_LINES + 500)),
            encoding="utf-8",
        )
        ctx = _ctx(tmp_path)

        result = await ReadTool().execute({"path": str(path)}, context=ctx)

        assert result.success is True
        lines = result.output.splitlines()
        assert len(lines) == MAX_DEFAULT_LINES + 2  # body + 2-line notice
        assert (
            f"showing lines 1-{MAX_DEFAULT_LINES} of {MAX_DEFAULT_LINES + 500}"
            in result.output
        )
        assert "Use offset/limit to read more." in result.output

    async def test_small_file_full_read_has_no_notice(self, tmp_path):
        path = tmp_path / "small.txt"
        path.write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")
        ctx = _ctx(tmp_path)

        result = await ReadTool().execute({"path": str(path)}, context=ctx)

        assert result.success is True
        assert "showing lines" not in result.output
        assert len(result.output.splitlines()) == 100

    async def test_explicit_limit_keeps_existing_notice_format(self, tmp_path):
        path = tmp_path / "big.txt"
        path.write_text("\n".join(f"line {i}" for i in range(3000)), encoding="utf-8")
        ctx = _ctx(tmp_path)

        result = await ReadTool().execute(
            {"path": str(path), "limit": 500}, context=ctx
        )

        assert result.success is True
        assert "showing lines 1-500 of 3000" in result.output
        assert "Use offset/limit to read more." not in result.output

    async def test_explicit_offset_plus_limit_navigates_beyond_default(self, tmp_path):
        path = tmp_path / "big.txt"
        path.write_text("\n".join(f"line {i}" for i in range(3000)), encoding="utf-8")
        ctx = _ctx(tmp_path)

        result = await ReadTool().execute(
            {"path": str(path), "offset": MAX_DEFAULT_LINES, "limit": 100}, context=ctx
        )

        assert result.success is True
        assert (
            f"showing lines {MAX_DEFAULT_LINES + 1}-{MAX_DEFAULT_LINES + 100} of 3000"
            in result.output
        )
