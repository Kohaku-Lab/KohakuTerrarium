"""Unit tests for atomic file replacement in ``WriteTool``."""

import os
import stat
import time
from pathlib import Path

import pytest

from kohakuterrarium.builtins.tools import write as write_module
from kohakuterrarium.builtins.tools.write import WriteTool
from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.utils.file_guard import FileReadState


def _context(tmp_path: Path, existing: Path | None = None) -> ToolContext:
    read_state = FileReadState()
    if existing is not None:
        read_state.record_read(
            str(existing), os.stat(existing).st_mtime_ns, False, time.time()
        )
    return ToolContext(
        agent_name="test",
        session=None,
        working_dir=tmp_path,
        file_read_state=read_state,
    )


class TestWriteToolAtomicReplace:
    async def test_success_replaces_content_and_removes_temp_file(self, tmp_path):
        path = tmp_path / "target.txt"
        reference = tmp_path / "reference.txt"
        reference.write_text("reference", encoding="utf-8")

        result = await WriteTool()._execute(
            {"path": str(path), "content": "complete"},
            context=_context(tmp_path),
        )

        assert result.exit_code == 0
        assert path.read_text(encoding="utf-8") == "complete"
        assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(
            reference.stat().st_mode
        )
        assert list(tmp_path.glob(".kt-write-*.tmp")) == []

    async def test_replace_failure_keeps_original_and_removes_temp_file(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "target.txt"
        path.write_text("original", encoding="utf-8")

        def _fail_replace(_source, _target):
            raise OSError("replace failed")

        monkeypatch.setattr(write_module.os, "replace", _fail_replace)
        result = await WriteTool()._execute(
            {"path": str(path), "content": "replacement"},
            context=_context(tmp_path, path),
        )

        assert result.error == "replace failed"
        assert path.read_text(encoding="utf-8") == "original"
        assert list(tmp_path.glob(".kt-write-*.tmp")) == []

    @pytest.mark.skipif(
        os.name == "nt", reason="Windows does not preserve POSIX permission bits"
    )
    async def test_existing_permission_mode_is_preserved(self, tmp_path):
        path = tmp_path / "target.txt"
        path.write_text("original", encoding="utf-8")
        path.chmod(0o640)

        result = await WriteTool()._execute(
            {"path": str(path), "content": "replacement"},
            context=_context(tmp_path, path),
        )

        assert result.exit_code == 0
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
