import json
import os
from pathlib import Path

from kohakuterrarium.builtins.tools.notebook_edit import NotebookEditTool
from kohakuterrarium.builtins.tools.notebook_read import NotebookReadTool
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


def _write_notebook(path: Path, cells: list[dict] | None = None) -> None:
    data = {
        "cells": (
            cells
            if cells is not None
            else [
                {
                    "cell_type": "markdown",
                    "id": "intro",
                    "metadata": {},
                    "source": ["# Title\n", "Notes"],
                },
                {
                    "cell_type": "code",
                    "id": "calc",
                    "metadata": {},
                    "source": "x = 1\nprint(x)",
                    "execution_count": 4,
                    "outputs": [
                        {"output_type": "stream", "name": "stdout", "text": "1\n"}
                    ],
                },
            ]
        ),
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


async def _read_then_edit(path: Path, context: ToolContext, args: dict):
    read_result = await NotebookReadTool().execute({"path": str(path)}, context=context)
    assert read_result.success, read_result.error
    payload = dict(args)
    payload["path"] = str(path)
    return await NotebookEditTool().execute(payload, context=context)


class TestNotebookRead:
    async def test_reads_cells_and_records_state(self, tmp_path: Path):
        target = tmp_path / "analysis.ipynb"
        _write_notebook(target)
        context = _make_context(tmp_path)

        result = await NotebookReadTool().execute(
            {"path": str(target)}, context=context
        )

        assert result.success, result.error
        assert "Notebook:" in result.output
        assert "Language: python" in result.output
        assert "[intro] index=0 type=markdown" in result.output
        assert "[calc] index=1 type=code execution_count=4" in result.output
        assert "[stream:stdout]" in result.output
        record = context.file_read_state.get(str(target))
        assert record is not None
        assert record.mtime_ns == os.stat(target).st_mtime_ns
        assert record.partial is False

    async def test_reads_synthetic_cell_id_without_real_ids(self, tmp_path: Path):
        target = tmp_path / "legacy.ipynb"
        _write_notebook(
            target,
            cells=[
                {"cell_type": "markdown", "metadata": {}, "source": "first"},
                {
                    "cell_type": "code",
                    "metadata": {},
                    "source": "second",
                    "outputs": [],
                },
            ],
        )
        context = _make_context(tmp_path)

        result = await NotebookReadTool().execute(
            {"path": str(target), "cell_id": "cell-1"}, context=context
        )

        assert result.success, result.error
        assert "[cell-1] index=1 type=code synthetic_id=true" in result.output
        assert "second" in result.output
        assert "first" not in result.output

    async def test_rejects_non_notebook(self, tmp_path: Path):
        target = tmp_path / "notes.txt"
        target.write_text("hello")
        context = _make_context(tmp_path)

        result = await NotebookReadTool().execute(
            {"path": str(target)}, context=context
        )

        assert not result.success
        assert ".ipynb" in result.error


class TestNotebookEditValidation:
    async def test_blocks_without_notebook_read(self, tmp_path: Path):
        target = tmp_path / "analysis.ipynb"
        _write_notebook(target)
        context = _make_context(tmp_path)

        result = await NotebookEditTool().execute(
            {"path": str(target), "cell_id": "intro", "new_source": "# New"},
            context=context,
        )

        assert not result.success
        assert "has not been read yet" in result.error

    async def test_rejects_strict_and_best_effort_together(self, tmp_path: Path):
        target = tmp_path / "analysis.ipynb"
        _write_notebook(target)
        context = _make_context(tmp_path)

        result = await _read_then_edit(
            target,
            context,
            {
                "strict": True,
                "best_effort": True,
                "cell_id": "intro",
                "new_source": "# New",
            },
        )

        assert not result.success
        assert "cannot be used together" in result.error

    async def test_stale_read_state_blocks_edit(self, tmp_path: Path):
        target = tmp_path / "analysis.ipynb"
        _write_notebook(target)
        context = _make_context(tmp_path)
        read_result = await NotebookReadTool().execute(
            {"path": str(target)}, context=context
        )
        assert read_result.success, read_result.error
        old_mtime_ns = os.stat(target).st_mtime_ns
        data = json.loads(target.read_text())
        data["metadata"]["external"] = True
        target.write_text(json.dumps(data), encoding="utf-8")
        os.utime(target, ns=(os.stat(target).st_atime_ns, old_mtime_ns + 1_000_000_000))

        result = await NotebookEditTool().execute(
            {"path": str(target), "cell_id": "intro", "new_source": "# New"},
            context=context,
        )

        assert not result.success
        assert "modified since last read" in result.error


class TestNotebookEditSingle:
    async def test_replaces_code_and_clears_outputs(self, tmp_path: Path):
        target = tmp_path / "analysis.ipynb"
        _write_notebook(target)
        context = _make_context(tmp_path)

        result = await _read_then_edit(
            target,
            context,
            {"cell_id": "calc", "new_source": "x = 2\nprint(x)"},
        )

        assert result.success, result.error
        data = json.loads(target.read_text(encoding="utf-8"))
        code_cell = data["cells"][1]
        assert code_cell["source"] == "x = 2\nprint(x)"
        assert code_cell["execution_count"] is None
        assert code_cell["outputs"] == []
        assert "edit[0]: ok: replaced cell calc" in result.output

    async def test_inserts_markdown_after_anchor(self, tmp_path: Path):
        target = tmp_path / "analysis.ipynb"
        _write_notebook(target)
        context = _make_context(tmp_path)

        result = await _read_then_edit(
            target,
            context,
            {
                "cell_id": "intro",
                "edit_mode": "insert",
                "cell_type": "markdown",
                "new_source": "## Details",
            },
        )

        assert result.success, result.error
        data = json.loads(target.read_text(encoding="utf-8"))
        assert len(data["cells"]) == 3
        assert data["cells"][1]["cell_type"] == "markdown"
        assert data["cells"][1]["source"] == "## Details"
        assert "id" in data["cells"][1]
        assert "inserted markdown cell" in result.output

    async def test_deletes_synthetic_cell(self, tmp_path: Path):
        target = tmp_path / "legacy.ipynb"
        _write_notebook(
            target,
            cells=[
                {"cell_type": "markdown", "metadata": {}, "source": "first"},
                {"cell_type": "markdown", "metadata": {}, "source": "delete me"},
            ],
        )
        context = _make_context(tmp_path)

        result = await _read_then_edit(
            target,
            context,
            {"cell_id": "cell-1", "edit_mode": "delete"},
        )

        assert result.success, result.error
        data = json.loads(target.read_text(encoding="utf-8"))
        assert len(data["cells"]) == 1
        assert data["cells"][0]["source"] == "first"


class TestNotebookEditBatch:
    async def test_batch_edits_atomically(self, tmp_path: Path):
        target = tmp_path / "analysis.ipynb"
        _write_notebook(target)
        context = _make_context(tmp_path)

        result = await _read_then_edit(
            target,
            context,
            {
                "edits": [
                    {
                        "cell_id": "intro",
                        "edit_mode": "replace",
                        "cell_type": "markdown",
                        "new_source": "# Updated",
                    },
                    {
                        "cell_id": "calc",
                        "edit_mode": "replace",
                        "new_source": "x = 10",
                    },
                ]
            },
        )

        assert result.success, result.error
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["cells"][0]["source"] == "# Updated"
        assert data["cells"][1]["source"] == "x = 10"
        assert "mode: strict" in result.output
        assert "applied: 2" in result.output

    async def test_strict_failure_keeps_file_unchanged(self, tmp_path: Path):
        target = tmp_path / "analysis.ipynb"
        _write_notebook(target)
        original = target.read_text(encoding="utf-8")
        context = _make_context(tmp_path)

        result = await _read_then_edit(
            target,
            context,
            {
                "edits": [
                    {"cell_id": "intro", "new_source": "# Updated"},
                    {"cell_id": "missing", "new_source": "oops"},
                ]
            },
        )

        assert not result.success
        assert "strict mode" in result.error
        assert target.read_text(encoding="utf-8") == original
        assert "No changes made" in result.output
        assert "edit[1]: error" in result.output

    async def test_best_effort_continues_past_failure(self, tmp_path: Path):
        target = tmp_path / "analysis.ipynb"
        _write_notebook(target)
        context = _make_context(tmp_path)

        result = await _read_then_edit(
            target,
            context,
            {
                "strict": False,
                "best_effort": True,
                "edits": [
                    {"cell_id": "intro", "new_source": "# Updated"},
                    {"cell_id": "missing", "new_source": "oops"},
                    {"cell_id": "calc", "new_source": "x = 42"},
                ],
            },
        )

        assert not result.success
        assert "completed with 1 failed edit" in result.error
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["cells"][0]["source"] == "# Updated"
        assert data["cells"][1]["source"] == "x = 42"
        assert "mode: best_effort" in result.output
