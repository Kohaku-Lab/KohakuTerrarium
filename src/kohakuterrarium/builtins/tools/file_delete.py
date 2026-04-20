"""
file_delete tool - remove files and (optionally) directories.

Structured alternative to ``bash rm``. Applies the same philosophy as
``write`` / ``edit``: the model must have *read* a file before destroying
it, so it knows what content is being lost. Directory deletion is opt-in
via ``recursive=true``.
"""

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)
from kohakuterrarium.utils.file_guard import check_read_before_write
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _lexical_abs_path(path_str: str, context: Any) -> Path:
    """Absolute path anchored to the agent's working dir *without* following
    symlinks. ``resolve_tool_path`` uses ``Path.resolve`` which would collapse
    a symlink to its target — unsafe for delete, where we must remove the
    link itself, not the thing it points to.
    """
    p = Path(path_str).expanduser()
    if not p.is_absolute() and context is not None:
        p = Path(context.working_dir) / p
    return Path(os.path.abspath(p))


def _clear_read_state(context: Any, target: Path) -> None:
    """Drop any file_read_state records under *target*."""
    if context is None or context.file_read_state is None:
        return
    state = context.file_read_state
    resolved = str(target.resolve()) if target.exists() else str(target)
    prefix = resolved + os.sep
    to_remove = [
        path
        for path in list(state._records.keys())  # type: ignore[attr-defined]
        if path == resolved or path.startswith(prefix)
    ]
    for path in to_remove:
        state._records.pop(path, None)  # type: ignore[attr-defined]


@register_builtin("file_delete")
class FileDeleteTool(BaseTool):
    """
    Delete a file or directory.

    Files require read-before-delete. Directories require ``recursive=true``
    to prevent accidental tree removal.
    """

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "file_delete"

    @property
    def description(self) -> str:
        return "Delete a file (must read first) or directory (requires recursive=true)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")

        path_arg = args.get("path", "")
        recursive = bool(args.get("recursive", False))

        if not path_arg:
            return ToolResult(error="No path provided")

        # Use lexical resolution so symlinks are deleted as-is (not followed).
        target = _lexical_abs_path(path_arg, context)

        if context and context.path_guard:
            msg = context.path_guard.check(str(target))
            if msg:
                return ToolResult(error=msg)

        is_symlink = target.is_symlink()
        if not target.exists() and not is_symlink:
            return ToolResult(error=f"Path not found: {path_arg}")

        # Symlinks: unlink directly. Read-before-delete does not apply —
        # the link's content is just a path string, and deleting the link
        # does not touch the file it points to.
        if is_symlink:
            try:
                await asyncio.to_thread(os.unlink, str(target))
            except PermissionError:
                return ToolResult(error=f"Permission denied: {path_arg}")
            except Exception as e:
                logger.error("file_delete failed", error=str(e))
                return ToolResult(error=str(e))
            _clear_read_state(context, target)
            logger.debug("Symlink deleted", target=str(target))
            return ToolResult(output=f"Deleted symlink {target}", exit_code=0)

        if target.is_file():
            msg = check_read_before_write(
                context.file_read_state if context else None, str(target)
            )
            if msg:
                return ToolResult(
                    error=msg.replace("write to", "delete").replace(
                        "writing", "deleting"
                    )
                )
            try:
                await asyncio.to_thread(os.unlink, str(target))
            except PermissionError:
                return ToolResult(error=f"Permission denied: {path_arg}")
            except Exception as e:
                logger.error("file_delete failed", error=str(e))
                return ToolResult(error=str(e))
            _clear_read_state(context, target)
            logger.debug("File deleted", target=str(target))
            return ToolResult(output=f"Deleted file {target}", exit_code=0)

        if target.is_dir():
            if not recursive:
                return ToolResult(
                    error=(
                        f"{path_arg} is a directory. Set recursive=true to "
                        "delete it and all its contents."
                    )
                )
            try:
                await asyncio.to_thread(shutil.rmtree, str(target))
            except PermissionError:
                return ToolResult(error=f"Permission denied: {path_arg}")
            except Exception as e:
                logger.error("file_delete failed", error=str(e))
                return ToolResult(error=str(e))
            _clear_read_state(context, target)
            logger.debug("Directory deleted", target=str(target))
            return ToolResult(output=f"Deleted directory {target}", exit_code=0)

        return ToolResult(error=f"Unsupported path type: {path_arg}")
