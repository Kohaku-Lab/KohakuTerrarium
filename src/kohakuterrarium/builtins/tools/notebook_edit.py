"""Batch edit Jupyter notebook cells."""

import asyncio
import copy
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.tools.edit import build_result_diff
from kohakuterrarium.builtins.tools.notebook_utils import (
    NotebookEditFailure,
    NotebookError,
    cell_display_id,
    load_notebook,
    make_cell,
    resolve_cell,
    set_cell_type,
    source_to_text,
    write_notebook,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
    resolve_tool_path,
)
from kohakuterrarium.utils.file_guard import check_read_before_write
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_VALID_MODES = {"replace", "insert", "delete"}
_VALID_CELL_TYPES = {"code", "markdown", "raw"}
_VALID_LOCATIONS = {"after", "before", "beginning", "end"}
MAX_RESULT_DIFF_LINES = 200


@dataclass(slots=True)
class EditStats:
    """Notebook edit application stats."""

    statuses: list[str]
    applied: int = 0
    failed: int = 0
    skipped: int = 0


def _normalize_mode(strict: bool, best_effort: bool) -> str:
    if strict and best_effort:
        raise ValueError("best_effort=true cannot be used together with strict=true")
    if best_effort:
        return "best_effort"
    if strict:
        return "strict"
    return "partial"


def _bool_arg(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return bool(value)


def _validate_edits(edits: Any) -> list[dict[str, Any]]:
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must be a non-empty array")
    normalized: list[dict[str, Any]] = []
    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ValueError(f"edit[{i}] must be an object")
        mode = edit.get("edit_mode", "replace")
        if mode not in _VALID_MODES:
            raise ValueError(f"edit[{i}].edit_mode must be replace, insert, or delete")
        cell_id = edit.get("cell_id")
        if cell_id is not None and not isinstance(cell_id, str):
            raise ValueError(f"edit[{i}].cell_id must be a string")
        cell_type = edit.get("cell_type")
        if cell_type is not None and cell_type not in _VALID_CELL_TYPES:
            raise ValueError(f"edit[{i}].cell_type must be code, markdown, or raw")
        if mode == "insert" and cell_type is None:
            raise ValueError(f"edit[{i}].cell_type is required for insert")
        if mode != "insert" and not cell_id:
            raise ValueError(f"edit[{i}].cell_id is required for {mode}")
        new_source = edit.get("new_source")
        if mode in {"replace", "insert"}:
            if new_source is None:
                raise ValueError(f"edit[{i}].new_source is required for {mode}")
            if not isinstance(new_source, str):
                raise ValueError(f"edit[{i}].new_source must be a string")
        elif new_source is not None and not isinstance(new_source, str):
            raise ValueError(f"edit[{i}].new_source must be a string")
        insert_location = edit.get("insert_location", "after")
        if insert_location not in _VALID_LOCATIONS:
            raise ValueError(
                f"edit[{i}].insert_location must be after, before, beginning, or end"
            )
        clear_outputs = _bool_arg(edit.get("clear_outputs"), True)
        normalized.append(
            {
                "edit_mode": mode,
                "cell_id": cell_id,
                "new_source": new_source,
                "cell_type": cell_type,
                "insert_location": insert_location,
                "clear_outputs": clear_outputs,
            }
        )
    return normalized


def _validate_single_edit_args(args: dict[str, Any]) -> list[dict[str, Any]]:
    edits = args.get("edits")
    if edits is not None:
        return _validate_edits(edits)
    mode = args.get("edit_mode", "replace")
    return _validate_edits(
        [
            {
                "edit_mode": mode,
                "cell_id": args.get("cell_id"),
                "new_source": args.get("new_source"),
                "cell_type": args.get("cell_type"),
                "insert_location": args.get("insert_location", "after"),
                "clear_outputs": args.get("clear_outputs", True),
            }
        ]
    )


def _normalized_notebook_json(data: dict[str, Any]) -> str:
    content = json.dumps(data, indent=1, ensure_ascii=False)
    if not content.endswith("\n"):
        content += "\n"
    return content


def _check_notebook_edit_guards(file_path: Path, context: Any) -> ToolResult | None:
    if context and context.path_guard:
        msg = context.path_guard.check(str(file_path))
        if msg:
            return ToolResult(error=msg)
    msg = check_read_before_write(
        context.file_read_state if context else None,
        str(file_path),
    )
    if msg:
        return ToolResult(error=msg)
    return None


def _apply_single_edit(
    data: dict[str, Any],
    edit: dict[str, Any],
    index: int,
) -> str:
    cells = data["cells"]
    mode = edit["edit_mode"]
    cell_id = edit["cell_id"]

    try:
        if mode == "insert":
            insert_at = _resolve_insert_index(cells, cell_id, edit["insert_location"])
            cell = make_cell(
                edit["cell_type"],
                edit["new_source"],
                data=data,
                cells=cells,
            )
            cells.insert(insert_at, cell)
            display_id, _ = cell_display_id(cell, insert_at)
            return f"ok: inserted {edit['cell_type']} cell {display_id} at index {insert_at}"

        resolved = resolve_cell(cells, cell_id)
        if mode == "delete":
            cells.pop(resolved.index)
            return f"ok: deleted cell {resolved.display_id} at index {resolved.index}"

        cell = resolved.cell
        before_cell = copy.deepcopy(cell)
        before_source = source_to_text(cell.get("source"))
        before_type = cell.get("cell_type")
        if edit["cell_type"]:
            set_cell_type(cell, edit["cell_type"], edit["clear_outputs"])
        cell["source"] = edit["new_source"]
        if cell.get("cell_type") == "code" and edit["clear_outputs"]:
            cell["execution_count"] = None
            cell["outputs"] = []
        if before_cell == cell:
            return f"ok: no change to cell {resolved.display_id}"
        if before_source == edit["new_source"] and before_type == cell.get("cell_type"):
            return f"ok: refreshed cell {resolved.display_id} at index {resolved.index}"
        return f"ok: replaced cell {resolved.display_id} at index {resolved.index}"
    except NotebookError as e:
        raise NotebookEditFailure(index, str(e)) from e


def _resolve_insert_index(
    cells: list[dict[str, Any]],
    cell_id: str | None,
    insert_location: str,
) -> int:
    if insert_location == "beginning":
        return 0
    if insert_location == "end":
        return len(cells)
    if not cell_id:
        return len(cells)
    resolved = resolve_cell(cells, cell_id)
    if insert_location == "before":
        return resolved.index
    return resolved.index + 1


def _build_output(
    file_path: Path,
    mode: str,
    stats: EditStats,
    original: str,
    final_content: str,
    file_changed: bool,
) -> str:
    header = []
    if file_changed:
        header.append(f"Edited {file_path}")
    else:
        header.append(f"No changes made to {file_path}")
    header.append(f"mode: {mode}")
    header.append(f"applied: {stats.applied}")
    header.append(f"failed: {stats.failed}")
    header.append(f"skipped: {stats.skipped}")
    header.append("")
    header.extend(stats.statuses)

    diff_text = build_result_diff(file_path, original, final_content)
    if diff_text:
        diff_lines = diff_text.splitlines()
        if len(diff_lines) > MAX_RESULT_DIFF_LINES:
            diff_text = "\n".join(diff_lines[:MAX_RESULT_DIFF_LINES])
            diff_text += f"\n... (diff truncated at {MAX_RESULT_DIFF_LINES} lines)"
        header.extend(["", diff_text])
    return "\n".join(header)


@register_builtin("notebook_edit")
class NotebookEditTool(BaseTool):
    """Apply one or more ordered cell edits to a Jupyter notebook."""

    needs_context = True
    require_manual_read = True
    is_concurrency_safe = False

    @property
    def tool_name(self) -> str:
        return "notebook_edit"

    @property
    def description(self) -> str:
        return (
            "Apply ordered cell edits to a Jupyter .ipynb notebook. "
            "Use info(notebook_edit) and notebook_read first."
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")
        path = args.get("path", "")
        if not path:
            return ToolResult(error="path is required")

        strict = args.get("strict", True)
        best_effort = args.get("best_effort", False)
        if not isinstance(strict, bool):
            return ToolResult(error="strict must be a boolean")
        if not isinstance(best_effort, bool):
            return ToolResult(error="best_effort must be a boolean")

        try:
            mode = _normalize_mode(strict, best_effort)
            edits = _validate_single_edit_args(args)
        except ValueError as e:
            return ToolResult(error=str(e))

        file_path = resolve_tool_path(path, context)
        if file_path.suffix.lower() != ".ipynb":
            return ToolResult(error="File must be a Jupyter notebook (.ipynb)")
        guard = _check_notebook_edit_guards(file_path, context)
        if guard:
            return guard
        if not file_path.exists():
            return ToolResult(error=f"File not found: {path}")
        if not file_path.is_file():
            return ToolResult(error=f"Not a file: {path}")

        try:
            notebook = await asyncio.to_thread(load_notebook, file_path)
            original = notebook.raw_content
            original_data = copy.deepcopy(notebook.data)
            stats = EditStats(statuses=[])
            encountered_failure = False

            for i, edit in enumerate(edits):
                if mode == "partial" and encountered_failure:
                    stats.statuses.append(f"edit[{i}]: skipped")
                    stats.skipped += 1
                    continue
                try:
                    status = _apply_single_edit(notebook.data, edit, i)
                    stats.statuses.append(f"edit[{i}]: {status}")
                    stats.applied += 1
                except NotebookEditFailure as e:
                    encountered_failure = True
                    stats.failed += 1
                    stats.statuses.append(f"edit[{e.index}]: error: {e.reason}")
                    if mode == "strict":
                        for j in range(i + 1, len(edits)):
                            stats.statuses.append(f"edit[{j}]: skipped")
                            stats.skipped += 1
                        output = _build_output(
                            file_path,
                            mode,
                            stats,
                            original,
                            original,
                            file_changed=False,
                        )
                        return ToolResult(
                            output=output,
                            error=(
                                f"notebook_edit failed in strict mode at edit[{e.index}] "
                                "(file unchanged)"
                            ),
                        )
                    if mode == "partial":
                        for j in range(i + 1, len(edits)):
                            stats.statuses.append(f"edit[{j}]: skipped")
                            stats.skipped += 1
                        break

            data_changed = notebook.data != original_data
            final_content = original
            file_changed = False
            if data_changed:
                final_content = _normalized_notebook_json(notebook.data)
                file_changed = final_content != original

            if file_changed:
                final_content = await asyncio.to_thread(
                    write_notebook, file_path, notebook.data
                )
                if context and context.file_read_state:
                    mtime_ns = os.stat(file_path).st_mtime_ns
                    context.file_read_state.record_read(
                        str(file_path),
                        mtime_ns,
                        False,
                        time.time(),
                    )

            output = _build_output(
                file_path,
                mode,
                stats,
                original,
                final_content,
                file_changed=file_changed,
            )
            if stats.failed:
                return ToolResult(
                    output=output,
                    error=f"notebook_edit completed with {stats.failed} failed edit(s)",
                )
            logger.debug(
                "Notebook edited",
                file_path=str(file_path),
                mode=mode,
                edits=len(edits),
                changed=file_changed,
            )
            return ToolResult(output=output, exit_code=0)
        except NotebookError as e:
            return ToolResult(error=str(e))
        except PermissionError:
            return ToolResult(error=f"Permission denied: {path}")
        except Exception as e:
            logger.error("Notebook edit failed", error=str(e))
            return ToolResult(error=str(e))
