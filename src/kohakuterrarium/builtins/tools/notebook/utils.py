"""Shared helpers for Jupyter notebook tools."""

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

NBFORMAT_CELL_ID_MINOR = 5
_VALID_CELL_TYPES = {"code", "markdown", "raw"}
_CELL_REF_RE = re.compile(r"^cell-(\d+)$")


@dataclass(slots=True)
class NotebookData:
    """Parsed notebook data plus original text."""

    path: Path
    raw_content: str
    data: dict[str, Any]


@dataclass(slots=True)
class ResolvedCell:
    """A resolved notebook cell reference."""

    index: int
    cell: dict[str, Any]
    display_id: str
    synthetic: bool


class NotebookError(Exception):
    """Notebook parsing or mutation error."""


class NotebookEditFailure(Exception):
    """Failure applying a single notebook edit."""

    def __init__(self, index: int, reason: str):
        self.index = index
        self.reason = reason
        super().__init__(reason)


def load_notebook(path: Path) -> NotebookData:
    """Load and validate a notebook JSON file."""
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise NotebookError(f"Notebook is not valid UTF-8 text: {e}") from e
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise NotebookError(f"Notebook is not valid JSON: {e}") from e
    validate_notebook(data)
    return NotebookData(path=path, raw_content=content, data=data)


def write_notebook(path: Path, data: dict[str, Any]) -> str:
    """Write notebook JSON in a stable, Jupyter-friendly format."""
    content = json.dumps(data, indent=1, ensure_ascii=False)
    if not content.endswith("\n"):
        content += "\n"
    path.write_text(content, encoding="utf-8")
    return content


def validate_notebook(data: Any) -> None:
    """Validate the minimum structure needed for notebook tools."""
    if not isinstance(data, dict):
        raise NotebookError("Notebook root must be a JSON object")
    cells = data.get("cells")
    if not isinstance(cells, list):
        raise NotebookError("Notebook must contain a top-level cells array")
    for idx, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise NotebookError(f"cells[{idx}] must be an object")
        cell_type = cell.get("cell_type")
        if not isinstance(cell_type, str):
            raise NotebookError(f"cells[{idx}].cell_type must be a string")
        if "source" not in cell:
            raise NotebookError(f"cells[{idx}] is missing source")


def notebook_language(data: dict[str, Any]) -> str:
    """Return the notebook language name, defaulting to python."""
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return "python"
    language_info = metadata.get("language_info")
    if isinstance(language_info, dict) and isinstance(language_info.get("name"), str):
        return language_info["name"]
    kernelspec = metadata.get("kernelspec")
    if isinstance(kernelspec, dict) and isinstance(kernelspec.get("language"), str):
        return kernelspec["language"]
    return "python"


def source_to_text(source: Any) -> str:
    """Normalize nbformat source fields to text."""
    if isinstance(source, str):
        return source
    if isinstance(source, list):
        return "".join(str(item) for item in source)
    if source is None:
        return ""
    return str(source)


def multiline_value_to_text(value: Any) -> str:
    """Normalize notebook multiline output values to text."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    if value is None:
        return ""
    return (
        json.dumps(value, ensure_ascii=False)
        if isinstance(value, (dict, list))
        else str(value)
    )


def cell_display_id(cell: dict[str, Any], index: int) -> tuple[str, bool]:
    """Return real cell id or synthetic cell-N reference."""
    cell_id = cell.get("id")
    if isinstance(cell_id, str) and cell_id:
        return cell_id, False
    return f"cell-{index}", True


def parse_synthetic_cell_id(cell_id: str) -> int | None:
    """Parse a synthetic cell-N id into an index."""
    match = _CELL_REF_RE.match(cell_id)
    if not match:
        return None
    return int(match.group(1))


def resolve_cell(cells: list[dict[str, Any]], cell_id: str) -> ResolvedCell:
    """Resolve an exact cell id or synthetic cell-N reference."""
    for idx, cell in enumerate(cells):
        if cell.get("id") == cell_id:
            display_id, synthetic = cell_display_id(cell, idx)
            return ResolvedCell(idx, cell, display_id, synthetic)
    synthetic_idx = parse_synthetic_cell_id(cell_id)
    if synthetic_idx is not None:
        if 0 <= synthetic_idx < len(cells):
            cell = cells[synthetic_idx]
            display_id, synthetic = cell_display_id(cell, synthetic_idx)
            return ResolvedCell(synthetic_idx, cell, display_id, synthetic)
        raise NotebookError(f"Cell index {synthetic_idx} does not exist")
    raise NotebookError(f"Cell with id {cell_id!r} not found")


def notebook_uses_cell_ids(data: dict[str, Any]) -> bool:
    """Return true when a notebook should receive ids on inserted cells."""
    nbformat = data.get("nbformat")
    minor = data.get("nbformat_minor")
    if isinstance(nbformat, int) and nbformat > 4:
        return True
    if isinstance(nbformat, int) and nbformat == 4:
        return isinstance(minor, int) and minor >= NBFORMAT_CELL_ID_MINOR
    return any(isinstance(cell.get("id"), str) for cell in data.get("cells", []))


def generate_cell_id(cells: list[dict[str, Any]]) -> str:
    """Generate a valid unique nbformat 4.5 cell id."""
    existing = {cell.get("id") for cell in cells if isinstance(cell.get("id"), str)}
    while True:
        cell_id = uuid.uuid4().hex[:8]
        if cell_id not in existing:
            return cell_id


def make_cell(
    cell_type: str,
    source: str,
    *,
    data: dict[str, Any],
    cells: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a new notebook cell."""
    if cell_type not in _VALID_CELL_TYPES:
        raise NotebookError("cell_type must be code, markdown, or raw")
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": {},
        "source": source,
    }
    if notebook_uses_cell_ids(data):
        cell["id"] = generate_cell_id(cells)
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def set_cell_type(cell: dict[str, Any], cell_type: str, clear_outputs: bool) -> None:
    """Update cell type and normalize code/non-code fields."""
    if cell_type not in _VALID_CELL_TYPES:
        raise NotebookError("cell_type must be code, markdown, or raw")
    cell["cell_type"] = cell_type
    if cell_type == "code":
        if clear_outputs:
            cell["execution_count"] = None
            cell["outputs"] = []
        else:
            cell.setdefault("execution_count", None)
            cell.setdefault("outputs", [])
        return
    cell.pop("execution_count", None)
    cell.pop("outputs", None)


def truncate_text(text: str, max_chars: int) -> str:
    """Truncate text with a compact notice."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... (truncated, {len(text)} chars total)"


def summarize_output(output: dict[str, Any], max_chars: int) -> str:
    """Summarize one notebook output."""
    output_type = output.get("output_type", "unknown")
    if output_type == "stream":
        name = output.get("name", "stream")
        text = truncate_text(multiline_value_to_text(output.get("text")), max_chars)
        return f"[{output_type}:{name}]\n{text}".rstrip()
    if output_type == "error":
        ename = output.get("ename", "Error")
        evalue = output.get("evalue", "")
        traceback = multiline_value_to_text(output.get("traceback"))
        text = f"{ename}: {evalue}\n{traceback}".strip()
        return f"[error]\n{truncate_text(text, max_chars)}".rstrip()
    if output_type in {"display_data", "execute_result"}:
        data = output.get("data")
        if not isinstance(data, dict):
            return f"[{output_type}]"
        text = multiline_value_to_text(data.get("text/plain"))
        parts = []
        if text:
            parts.append(truncate_text(text, max_chars))
        for mime in ("image/png", "image/jpeg", "image/svg+xml"):
            if mime in data:
                value = multiline_value_to_text(data.get(mime))
                parts.append(f"[{mime} output omitted: {len(value)} chars]")
        if not parts:
            parts.append(f"mime outputs: {', '.join(sorted(data.keys()))}")
        return f"[{output_type}]\n" + "\n".join(parts)
    return f"[{output_type}]"
