"""
file_move tool - move or rename files and directories.

Structured alternative to ``bash mv``. Applies the same safety guards as
``write`` / ``edit``:

- Path boundary guard on both source and destination.
- Read-before-move on destination when it already exists and ``overwrite`` is
  set (so the model cannot silently clobber tracked content).
- ``file_read_state`` is migrated from source to destination on success.
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
    symlinks. Matches ``bash mv`` semantics where a symlink src is moved as
    the link itself, not followed to its target.
    """
    p = Path(path_str).expanduser()
    if not p.is_absolute() and context is not None:
        p = Path(context.working_dir) / p
    return Path(os.path.abspath(p))


def _migrate_read_state(context: Any, src: Path, dst: Path) -> None:
    """Move or clear file_read_state records affected by a move.

    Files: migrate the single record from src -> dst if present.
    Directories: clear every record whose path sits under src. We do not
    rewrite them to dst because the model should re-read after a bulk move.
    """
    if context is None or context.file_read_state is None:
        return
    state = context.file_read_state
    src_key = str(src)
    if dst.is_dir():
        prefix = src_key + os.sep
        to_remove = [
            path
            for path in list(state._records.keys())  # type: ignore[attr-defined]
            if path == src_key or path.startswith(prefix)
        ]
        for path in to_remove:
            state._records.pop(path, None)  # type: ignore[attr-defined]
        return
    record = state.get(src_key)
    if record is None:
        return
    state._records.pop(src_key, None)  # type: ignore[attr-defined]
    state.record_read(
        str(dst),
        mtime_ns=record.mtime_ns,
        partial=record.partial,
        timestamp=record.timestamp,
    )


@register_builtin("file_move")
class FileMoveTool(BaseTool):
    """
    Move or rename a file or directory.

    Cross-device moves fall back to copy + delete transparently via
    ``shutil.move``.
    """

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "file_move"

    @property
    def description(self) -> str:
        return "Move or rename a file/directory (set overwrite=true to replace destination)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        context = kwargs.get("context")

        src_arg = args.get("src", "") or args.get("source", "")
        dst_arg = args.get("dst", "") or args.get("destination", "")
        overwrite = bool(args.get("overwrite", False))

        if not src_arg:
            return ToolResult(error="No src provided")
        if not dst_arg:
            return ToolResult(error="No dst provided")

        src = _lexical_abs_path(src_arg, context)
        dst = _lexical_abs_path(dst_arg, context)

        if context and context.path_guard:
            for path in (src, dst):
                msg = context.path_guard.check(str(path))
                if msg:
                    return ToolResult(error=msg)

        if not src.exists() and not src.is_symlink():
            return ToolResult(error=f"Source not found: {src_arg}")

        if src == dst:
            return ToolResult(error="src and dst resolve to the same path")

        if dst.exists() or dst.is_symlink():
            if not overwrite:
                return ToolResult(
                    error=(
                        f"Destination already exists: {dst_arg}. "
                        "Set overwrite=true to replace it, or choose a different dst."
                    )
                )
            if dst.is_file() and not dst.is_symlink():
                msg = check_read_before_write(
                    context.file_read_state if context else None, str(dst)
                )
                if msg:
                    return ToolResult(error=msg)

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.move, str(src), str(dst))
        except PermissionError:
            return ToolResult(error=f"Permission denied moving {src_arg} -> {dst_arg}")
        except Exception as e:
            logger.error("file_move failed", error=str(e))
            return ToolResult(error=str(e))

        _migrate_read_state(context, src, dst)

        if dst.is_symlink():
            kind = "symlink"
        elif dst.is_dir():
            kind = "directory"
        else:
            kind = "file"
        logger.debug("File moved", src=str(src), dst=str(dst), kind=kind)
        return ToolResult(
            output=f"Moved {kind} {src} -> {dst}",
            exit_code=0,
        )
