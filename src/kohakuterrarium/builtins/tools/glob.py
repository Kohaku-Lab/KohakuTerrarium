"""Find files with gitignore-aware, bounded glob traversal."""

import asyncio
import os
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
    resolve_tool_path,
)
from kohakuterrarium.utils.file_walk import iter_matching_files_stat
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@register_builtin("glob")
class GlobTool(BaseTool):
    """Find files by glob pattern and sort them by modification time."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return "Find files by path pattern, newest first. Use when you know the name or extension. Not for searching file contents - use grep."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Find files matching pattern."""
        context = kwargs.get("context")

        pattern = args.get("pattern", "")
        if not pattern:
            return ToolResult(error="No pattern provided")

        base_path = args.get("path", ".")
        base = resolve_tool_path(base_path, context)

        if context and context.path_guard:
            msg = context.path_guard.check(str(base))
            if msg:
                return ToolResult(error=msg)

        if not base.exists():
            return ToolResult(error=f"Path not found: {base_path}")

        limit = int(args.get("limit", 100))
        follow_gitignore = str(args.get("gitignore", "true")).lower() not in (
            "false",
            "no",
            "0",
        )

        try:
            result = await asyncio.to_thread(
                self._find_files, base, pattern, limit, follow_gitignore
            )
            return result

        except Exception as e:
            logger.error("Glob failed", error=str(e))
            return ToolResult(error=str(e))

    def _find_files(
        self,
        base: Path,
        pattern: str,
        limit: int,
        follow_gitignore: bool,
    ) -> ToolResult:
        """Collect and order matching files without blocking the event loop."""
        # Collection exceeds the display limit to preserve useful recency sorting,
        # while retaining an upper bound for large trees.
        cap = max(limit * 10, 5_000) if limit > 0 else 50_000

        entries: list[tuple[Path, os.stat_result | None]] = list(
            iter_matching_files_stat(
                base,
                pattern,
                gitignore=follow_gitignore,
                cap=cap,
            )
        )
        hit_cap = len(entries) >= cap

        # Stats were carried out of the walk itself (free on Windows);
        # a file that vanished mid-walk sorts oldest instead of failing
        # the whole search.
        entries.sort(
            key=lambda e: e[1].st_mtime if e[1] is not None else 0.0, reverse=True
        )

        total = len(entries)
        if limit > 0 and total > limit:
            entries = entries[:limit]

        output_lines = []
        for match, _stat in entries:
            try:
                rel_path = match.relative_to(base)
            except ValueError:
                rel_path = match
            output_lines.append(str(rel_path))

        output = "\n".join(output_lines)

        if hit_cap:
            output += (
                f"\n\n... (showing {len(entries)} of {total} collected, "
                f"capped at {cap}; more may exist — narrow your pattern)"
            )
        elif total > len(entries):
            output += f"\n\n... ({total} total, showing {len(entries)})"

        logger.debug(
            "Glob search",
            pattern=pattern,
            matches=len(entries),
        )

        return ToolResult(output=output or "(no matches)", exit_code=0)
