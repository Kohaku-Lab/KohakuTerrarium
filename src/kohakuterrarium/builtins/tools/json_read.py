"""JSON read tool - read and query JSON files."""

import json
from typing import Any

import aiofiles

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
    resolve_tool_path,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _split_path(path: str) -> list[str | int]:
    """Split a dot-path into components, handling array indices."""
    parts: list[str | int] = []
    for segment in path.split("."):
        if not segment:
            continue
        # Check for array index: key[0]
        if "[" in segment:
            key, rest = segment.split("[", 1)
            if key:
                parts.append(key)
            idx = rest.rstrip("]")
            parts.append(int(idx))
        else:
            parts.append(segment)
    return parts


def _resolve_path(data: Any, query: str) -> Any:
    """
    Resolve a simple dot-path query against JSON data.

    Supports: .key, .key.nested, .array[0], .array[0].field
    """
    if not query or query == ".":
        return data

    # Remove leading dot
    path = query.lstrip(".")
    current = data

    for part in _split_path(path):
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                raise KeyError(f"Index {part} out of range")
            current = current[part]
        elif isinstance(current, dict):
            if part not in current:
                raise KeyError(f"Key '{part}' not found")
            current = current[part]
        else:
            raise KeyError(f"Cannot index into {type(current).__name__} with '{part}'")

    return current


@register_builtin("json_read")
class JsonReadTool(BaseTool):
    """Read and query JSON files with path expressions."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "json_read"

    @property
    def description(self) -> str:
        return "Read and query JSON files"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Read and optionally query a JSON file."""
        context = kwargs.get("context")
        path = args.get("path", "")
        query = args.get("query", ".")

        if not path:
            return ToolResult(error="Path is required")

        file_path = resolve_tool_path(path, context)
        if not file_path.exists():
            return ToolResult(error=f"File not found: {path}")

        if not file_path.is_file():
            return ToolResult(error=f"Not a file: {path}")

        try:
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                content = await f.read()
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return ToolResult(error=f"Invalid JSON: {e}")
        except PermissionError:
            return ToolResult(error=f"Permission denied: {path}")
        except Exception as e:
            logger.error("JSON read failed", error=str(e))
            return ToolResult(error=str(e))

        # Apply query
        try:
            result = _resolve_path(data, query)
        except KeyError as e:
            return ToolResult(error=f"Query failed: {e}")

        # Format output
        if isinstance(result, (dict, list)):
            output = json.dumps(result, indent=2, ensure_ascii=False)
        else:
            output = str(result)

        # Truncate if too large
        if len(output) > 50000:
            output = output[:50000] + "\n... (truncated)"

        logger.debug(
            "JSON file read",
            file_path=str(file_path),
            query=query,
        )

        return ToolResult(output=output, exit_code=0)
