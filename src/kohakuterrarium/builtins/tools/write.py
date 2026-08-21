"""Guarded file creation and replacement."""

import os
import secrets
import stat
import time
from pathlib import Path
from typing import Any

import aiofiles

from kohakuterrarium.builtins.tools.canvas_preview import build_canvas_preview
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
    """Write complete file content, creating parent directories as needed."""

    needs_context = True
    # Serial execution prevents related paths from being mutated concurrently.
    is_concurrency_safe = False

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

        file_path = resolve_tool_path(path, context)

        if context and context.path_guard:
            msg = context.path_guard.check(str(file_path))
            if msg:
                return ToolResult(error=msg)

        msg = check_read_before_write(
            context.file_read_state if context else None, str(file_path)
        )
        if msg:
            return ToolResult(error=msg)

        temp_path: Path | None = None
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)

            exists = file_path.exists()
            existing_mode = stat.S_IMODE(file_path.stat().st_mode) if exists else None

            temp_path = file_path.parent / f".kt-write-{secrets.token_hex(12)}.tmp"
            async with aiofiles.open(temp_path, "x", encoding="utf-8") as f:
                await f.write(content)

            if existing_mode is not None:
                os.chmod(temp_path, existing_mode)
            os.replace(temp_path, file_path)
            temp_path = None

            action = "Updated" if exists else "Created"
            lines = content.count("\n") + 1 if content else 0

            logger.debug(
                "File written",
                file_path=str(file_path),
                action=action.lower(),
                lines=lines,
            )

            # The completed write becomes the new read baseline for later guards.
            if context and context.file_read_state:
                mtime_ns = os.stat(file_path).st_mtime_ns
                context.file_read_state.record_read(
                    str(file_path), mtime_ns, False, time.time()
                )

            return ToolResult(
                output=f"{action} {file_path} ({lines} lines, {len(content)} bytes)",
                exit_code=0,
                metadata={
                    # Inline content lets the canvas reflect the write without a
                    # second file fetch that could observe a later filesystem state.
                    "canvas_preview": build_canvas_preview(
                        kind="write",
                        file_path=str(file_path),
                        content=content,
                    ),
                },
            )

        except PermissionError:
            return ToolResult(error=f"Permission denied: {path}")
        except Exception as e:
            logger.error("Write failed", error=str(e))
            return ToolResult(error=str(e))
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.warning(
                        "Failed to remove temporary write file",
                        file_path=str(temp_path),
                        error=str(e),
                    )
