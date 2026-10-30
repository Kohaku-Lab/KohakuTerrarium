"""Unit tests for :mod:`kohakuterrarium.builtins.tools.files.read`."""

import base64
import io

import pymupdf
import pytest
from PIL import Image

from kohakuterrarium.builtins.tools.files.read import (
    MAX_DEFAULT_LINES,
    ReadTool,
    encode_page,
)
from kohakuterrarium.modules.tool.base import ToolConfig, ToolContext
from kohakuterrarium.utils.file.guard import PathBoundaryGuard


def _ctx(tmp_path):
    return ToolContext(agent_name="agent", session=None, working_dir=tmp_path)


def _guarded_ctx(workspace):
    return ToolContext(
        agent_name="agent",
        session=None,
        working_dir=workspace,
        path_guard=PathBoundaryGuard(cwd=workspace, mode="block"),
    )


class TestReadPathGuard:
    @pytest.mark.parametrize("suffix", [".png", ".pdf"])
    async def test_blocks_multimodal_files_before_type_dispatch(self, tmp_path, suffix):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / f"secret{suffix}"
        outside.write_bytes(b"not real media")

        result = await ReadTool().execute(
            {"path": str(outside)}, context=_guarded_ctx(workspace)
        )

        assert result.success is False
        assert "Access denied" in result.error
        assert str(workspace) in result.error


class TestReadImage:
    @pytest.mark.parametrize("mutation", ["delete", "overwrite"])
    async def test_image_snapshot_survives_source_changes(self, tmp_path, mutation):
        path = tmp_path / "shot.png"
        Image.new("RGB", (2, 2), "red").save(path)
        original = path.read_bytes()

        result = await ReadTool().execute({"path": str(path)}, context=_ctx(tmp_path))
        if mutation == "delete":
            path.unlink()
        else:
            Image.new("RGB", (2, 2), "blue").save(path)

        assert result.success is True
        image = next(p for p in result.output if p.type == "image_url")
        assert (
            image.url == "data:image/png;base64," + base64.b64encode(original).decode()
        )
        assert image.source_type == "file"
        assert image.source_name == "shot.png"

    def test_read_snapshot_is_persisted_and_folds_away(self):
        policy = ReadTool().media_policy
        assert policy.persist is True
        assert policy.pinned is False
        assert policy.inline_fallback is True


def _pdf(path, color=(0, 0, 0)):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((40, 80), "Quarterly report: revenue grew", color=color)
    page.draw_rect(pymupdf.Rect(40, 120, 260, 360), color=color, width=2)
    doc.save(path)
    doc.close()


def _page_bytes(image):
    return base64.b64decode(image.url.split(",", 1)[1])


class TestReadPdfPages:
    async def test_auto_keeps_the_smaller_encoding_of_a_gray_page(self, tmp_path):
        path = tmp_path / "doc.pdf"
        _pdf(path)
        sizes = {}
        for fmt in ("png", "jpeg", "auto"):
            tool = ReadTool(config=ToolConfig(extra={"pdf_image_format": fmt}))
            result = await tool.execute({"path": str(path)}, context=_ctx(tmp_path))
            assert result.success is True
            image = next(p for p in result.output if p.type == "image_url")
            assert image.source_type == "pdf_page"
            data = _page_bytes(image)
            with Image.open(io.BytesIO(data)) as decoded:
                assert decoded.mode == "L"
                assert decoded.width in (416, 417) and decoded.height in (555, 556)
                assert image.url.startswith(f"data:image/{decoded.format.lower()};")
            sizes[fmt] = len(data)
        assert sizes["auto"] == min(sizes["png"], sizes["jpeg"])

    def test_color_page_keeps_rgb_and_gray_page_drops_to_one_channel(self):
        gray = bytes([90, 90, 90]) * 16
        color = bytes([200, 30, 30]) * 16
        for samples, mode in ((gray, "L"), (color, "RGB")):
            data, _ = encode_page(4, 4, samples, "png", 75)
            with Image.open(io.BytesIO(data)) as decoded:
                assert decoded.mode == mode

    async def test_unknown_format_is_refused(self, tmp_path):
        path = tmp_path / "doc.pdf"
        _pdf(path)
        tool = ReadTool(config=ToolConfig(extra={"pdf_image_format": "gif"}))
        result = await tool.execute({"path": str(path)}, context=_ctx(tmp_path))
        assert result.success is False
        assert "pdf_image_format" in result.error


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

    async def test_byte_cap_aligned_with_executor_max_output(self, tmp_path):
        # A file under the default line guard but over the byte cap must
        # truncate at 256 * 1024 bytes (aligned with the executor's
        # max_output default).
        path = tmp_path / "wide.txt"
        path.write_text(
            "\n".join(f"row {i} " + "x" * 400 for i in range(1000)),
            encoding="utf-8",
        )
        ctx = _ctx(tmp_path)

        result = await ReadTool().execute({"path": str(path)}, context=ctx)

        assert result.success is True
        assert "truncated at 262144 bytes" in result.output
        assert "Use offset/limit to read specific sections." in result.output
