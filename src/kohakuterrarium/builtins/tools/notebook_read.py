"""Read Jupyter notebooks as structured cells."""

import asyncio
import os
import time
from typing import Any

from kohakuterrarium.builtins.tools.notebook_utils import (
    NotebookError,
    cell_display_id,
    load_notebook,
    notebook_language,
    resolve_cell,
    source_to_text,
    summarize_output,
    truncate_text,
)
from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
    resolve_tool_path,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_VALID_OUTPUT_MODES = {"none", "summary", "all"}


def _bool_arg(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return bool(value)


def _int_arg(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


@register_builtin("notebook_read")
class NotebookReadTool(BaseTool):
    """Read a Jupyter notebook as a concise cell listing."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "notebook_read"

    @property
    def description(self) -> str:
        return "Read a Jupyter .ipynb notebook as cells (required before notebook_edit)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")
        path = args.get("path", "")
        if not path:
            return ToolResult(error="path is required")

        file_path = resolve_tool_path(path, context)
        if context and context.path_guard:
            msg = context.path_guard.check(str(file_path))
            if msg:
                return ToolResult(error=msg)
        if file_path.suffix.lower() != ".ipynb":
            return ToolResult(error="File must be a Jupyter notebook (.ipynb)")
        if not file_path.exists():
            return ToolResult(error=f"File not found: {path}")
        if not file_path.is_file():
            return ToolResult(error=f"Not a file: {path}")

        try:
            offset = _int_arg(args.get("offset"), 0)
            limit = _int_arg(args.get("limit"), 0)
            max_source_chars = _int_arg(args.get("max_source_chars"), 8000)
            max_output_chars = _int_arg(args.get("max_output_chars"), 4000)
            include_outputs = str(args.get("include_outputs", "summary"))
            include_metadata = _bool_arg(args.get("include_metadata"), False)
        except (TypeError, ValueError) as e:
            return ToolResult(error=f"Invalid numeric/boolean argument: {e}")

        if offset < 0:
            return ToolResult(error="offset must be >= 0")
        if limit < 0:
            return ToolResult(error="limit must be >= 0")
        if include_outputs not in _VALID_OUTPUT_MODES:
            return ToolResult(
                error="include_outputs must be one of: none, summary, all"
            )

        try:
            notebook = await asyncio.to_thread(load_notebook, file_path)
        except NotebookError as e:
            return ToolResult(error=str(e))
        except PermissionError:
            return ToolResult(error=f"Permission denied: {path}")

        data = notebook.data
        cells = data["cells"]
        selected = cells
        cell_id = args.get("cell_id")
        if cell_id:
            try:
                resolved = resolve_cell(cells, str(cell_id))
            except NotebookError as e:
                return ToolResult(error=str(e))
            selected_with_indices = [(resolved.index, resolved.cell)]
        else:
            start = min(offset, len(cells))
            end = len(cells) if limit == 0 else min(start + limit, len(cells))
            selected = cells[start:end]
            selected_with_indices = list(enumerate(selected, start=start))

        output = _render_notebook(
            path=str(file_path),
            data=data,
            selected_with_indices=selected_with_indices,
            total_cells=len(cells),
            offset=offset,
            limit=limit,
            include_outputs=include_outputs,
            include_metadata=include_metadata,
            max_source_chars=max_source_chars,
            max_output_chars=max_output_chars,
        )

        if context and context.file_read_state:
            mtime_ns = os.stat(file_path).st_mtime_ns
            # Reading any notebook view parses the full file, so record it as a full read.
            context.file_read_state.record_read(
                str(file_path), mtime_ns, False, time.time()
            )

        logger.debug(
            "Notebook read",
            file_path=str(file_path),
            cells_read=len(selected_with_indices),
        )
        return ToolResult(output=output, exit_code=0)


def _render_notebook(
    *,
    path: str,
    data: dict[str, Any],
    selected_with_indices: list[tuple[int, dict[str, Any]]],
    total_cells: int,
    offset: int,
    limit: int,
    include_outputs: str,
    include_metadata: bool,
    max_source_chars: int,
    max_output_chars: int,
) -> str:
    language = notebook_language(data)
    nbformat = data.get("nbformat", "?")
    nbformat_minor = data.get("nbformat_minor", "?")
    lines = [
        f"Notebook: {path}",
        f"Language: {language}",
        f"Format: nbformat {nbformat}.{nbformat_minor}",
        f"Cells: {total_cells}",
        "",
    ]

    for index, cell in selected_with_indices:
        display_id, synthetic = cell_display_id(cell, index)
        cell_type = cell.get("cell_type", "unknown")
        suffix = " synthetic_id=true" if synthetic else ""
        header = f"[{display_id}] index={index} type={cell_type}{suffix}"
        if cell_type == "code" and cell.get("execution_count") is not None:
            header += f" execution_count={cell.get('execution_count')}"
        lines.append(header)
        if include_metadata and cell.get("metadata"):
            lines.append(f"metadata: {cell.get('metadata')}")
        source = source_to_text(cell.get("source"))
        lines.append(truncate_text(source, max_source_chars))

        if include_outputs != "none" and cell_type == "code":
            outputs = cell.get("outputs")
            if isinstance(outputs, list) and outputs:
                output_items = outputs if include_outputs == "all" else outputs[:3]
                lines.append("Outputs:")
                for output in output_items:
                    if isinstance(output, dict):
                        lines.append(summarize_output(output, max_output_chars))
                if include_outputs == "summary" and len(outputs) > len(output_items):
                    lines.append(
                        f"... ({len(outputs) - len(output_items)} more output(s) omitted)"
                    )
        lines.append("")

    if limit > 0 and offset + limit < total_cells:
        lines.append(
            f"... (showing cells {offset}-{offset + len(selected_with_indices) - 1} of {total_cells})"
        )
    return "\n".join(lines).rstrip()
