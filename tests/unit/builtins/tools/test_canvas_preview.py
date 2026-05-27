"""Unit tests for the canvas_preview metadata helper + the write /
edit / multi_edit tools' new ``canvas_preview`` payload."""

from pathlib import Path

import pytest

from kohakuterrarium.builtins.tools.canvas_preview import (
    PREVIEW_MAX_BYTES,
    build_canvas_preview,
    lang_for_path,
)
from kohakuterrarium.builtins.tools.edit import EditTool
from kohakuterrarium.builtins.tools.write import WriteTool
from kohakuterrarium.modules.tool.base import ToolContext


def _ctx(tmp_path: Path, file_read_state=None) -> ToolContext:
    return ToolContext(
        agent_name="test",
        session=None,
        working_dir=tmp_path,
        agent=None,
        file_read_state=file_read_state,
    )


class TestLangForPath:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("/x/foo.py", "python"),
            ("/x/foo.js", "javascript"),
            ("/x/foo.tsx", "typescript"),
            ("/x/foo.vue", "vue"),
            ("/x/Foo.MD", "markdown"),
            ("/x/no_ext", "text"),
            ("/x/foo.weirdext", "text"),
        ],
    )
    def test_extension_map(self, name, expected):
        assert lang_for_path(name) == expected


class TestBuildCanvasPreview:
    def test_small_file_keeps_full_content(self):
        out = build_canvas_preview("write", "/repo/foo.py", "print(1)\n")
        assert out == {
            "kind": "write",
            "file_path": "/repo/foo.py",
            "lang": "python",
            "content": "print(1)\n",
            "bytes": len(b"print(1)\n"),
            "truncated": False,
        }

    def test_huge_file_drops_content_and_flags_truncated(self):
        # Behaviour assert (not shape): a file larger than the cap must
        # surface ``content=None, truncated=True`` so the FE can offer a
        # "load via /files" stub instead of an empty bubble.
        huge = "x" * (PREVIEW_MAX_BYTES + 1)
        out = build_canvas_preview("edit", "/repo/huge.txt", huge)
        assert out["content"] is None
        assert out["truncated"] is True
        assert out["bytes"] == PREVIEW_MAX_BYTES + 1

    def test_none_content_propagates(self):
        # Edge case for tools that mutate the file but don't know the
        # final content (none today, but the helper supports it).
        out = build_canvas_preview("edit", "/repo/foo.txt", None)
        assert out["content"] is None
        assert out["truncated"] is False
        assert out["bytes"] == 0


class TestWriteToolCanvasPreview:
    async def test_write_attaches_canvas_preview(self, tmp_path):
        tool = WriteTool()
        path = tmp_path / "hello.py"
        result = await tool._execute(
            {"path": str(path), "content": "print('hi')\n"},
            context=_ctx(tmp_path),
        )
        assert result.exit_code == 0
        preview = result.metadata.get("canvas_preview")
        assert preview is not None
        assert preview["kind"] == "write"
        assert preview["file_path"] == str(path)
        assert preview["lang"] == "python"
        assert preview["content"] == "print('hi')\n"
        assert preview["truncated"] is False


class TestEditToolCanvasPreview:
    async def test_search_replace_attaches_canvas_preview(self, tmp_path):
        from kohakuterrarium.utils.file_guard import FileReadState

        path = tmp_path / "edit_target.py"
        path.write_text("def a():\n    return 1\n")
        read_state = FileReadState()
        import os
        import time

        read_state.record_read(str(path), os.stat(path).st_mtime_ns, False, time.time())

        tool = EditTool()
        result = await tool._execute(
            {
                "path": str(path),
                "old": "return 1",
                "new": "return 42",
            },
            context=_ctx(tmp_path, file_read_state=read_state),
        )
        assert result.exit_code == 0, result.error
        preview = result.metadata.get("canvas_preview")
        assert preview is not None
        assert preview["kind"] == "edit"
        assert preview["file_path"].endswith("edit_target.py")
        # The post-edit content must reflect the replacement — that's
        # what the canvas panel ends up showing.
        assert "return 42" in (preview["content"] or "")
        assert preview["lang"] == "python"
