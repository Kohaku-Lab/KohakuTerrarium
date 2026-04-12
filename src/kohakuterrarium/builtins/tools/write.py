"""
Write tool - write content to files.
"""

import os
import time
from typing import Any

import aiofiles

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


@register_builtin("write")
class WriteTool(BaseTool):
    """
    Tool for writing/creating files.

    Creates parent directories if needed.
    """

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "write"

    @property
    def description(self) -> str:
        return "Write content to a file (must read first if file exists)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Write content to file."""
        context = kwargs.get("context")

        path = args.get("path", "")
        content = args.get("content", "")

        if not path:
            return ToolResult(error="No path provided")

        # Resolve path
        file_path = resolve_tool_path(path, context)

        # Path boundary guard
        if context and context.path_guard:
            msg = context.path_guard.check(str(file_path))
            if msg:
                return ToolResult(error=msg)

        # Read-before-write guard
        msg = check_read_before_write(
            context.file_read_state if context else None, str(file_path)
        )
        if msg:
            return ToolResult(error=msg)

        try:
            # Create parent directories if needed
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Check if file exists for logging
            exists = file_path.exists()

            # Write content
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(content)

            action = "Updated" if exists else "Created"
            lines = content.count("\n") + 1 if content else 0

            logger.debug(
                "File written",
                file_path=str(file_path),
                action=action.lower(),
                lines=lines,
            )

            # Update file_read_state with new mtime
            if context and context.file_read_state:
                mtime_ns = os.stat(file_path).st_mtime_ns
                context.file_read_state.record_read(
                    str(file_path), mtime_ns, False, time.time()
                )

            return ToolResult(
                output=f"{action} {file_path} ({lines} lines, {len(content)} bytes)",
                exit_code=0,
            )

        except PermissionError:
            return ToolResult(error=f"Permission denied: {path}")
        except Exception as e:
            logger.error("Write failed", error=str(e))
            return ToolResult(error=str(e))
