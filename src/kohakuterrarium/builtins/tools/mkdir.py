"""
mkdir tool - create directories.

Structured alternative to ``bash mkdir``. Defaults match the pattern used
elsewhere in the codebase (``Path.mkdir(parents=True, exist_ok=True)``):
intermediate directories are created automatically, and an existing
directory is reported (not an error) unless ``error_if_exists=true`` is set.
"""

import asyncio
from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
    resolve_tool_path,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@register_builtin("mkdir")
class MkdirTool(BaseTool):
    """
    Create a directory.

    Parent directories are created by default. Existing directories are
    treated as success unless ``error_if_exists=true``.
    """

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "mkdir"

    @property
    def description(self) -> str:
        return "Create a directory (parents and existing dirs OK by default)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")

        path_arg = args.get("path", "")
        parents = bool(args.get("parents", True))
        error_if_exists = bool(args.get("error_if_exists", False))

        if not path_arg:
            return ToolResult(error="No path provided")

        target = resolve_tool_path(path_arg, context)

        if context and context.path_guard:
            msg = context.path_guard.check(str(target))
            if msg:
                return ToolResult(error=msg)

        if target.exists():
            if target.is_dir():
                if error_if_exists:
                    return ToolResult(error=f"Directory already exists: {path_arg}")
                return ToolResult(
                    output=f"Directory already exists: {target}",
                    exit_code=0,
                )
            return ToolResult(error=f"Path exists and is not a directory: {path_arg}")

        try:
            await asyncio.to_thread(
                target.mkdir, parents=parents, exist_ok=not error_if_exists
            )
        except FileNotFoundError:
            return ToolResult(
                error=(
                    f"Parent directory missing for {path_arg}. "
                    "Set parents=true (the default) to create it."
                )
            )
        except PermissionError:
            return ToolResult(error=f"Permission denied: {path_arg}")
        except Exception as e:
            logger.error("mkdir failed", error=str(e))
            return ToolResult(error=str(e))

        logger.debug("Directory created", target=str(target))
        return ToolResult(output=f"Created directory {target}", exit_code=0)
