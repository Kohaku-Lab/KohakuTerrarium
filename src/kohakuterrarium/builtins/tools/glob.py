"""
Glob tool - find files matching patterns.

Respects ``.gitignore`` by default and caps collection to avoid
materialising the entire directory tree on large projects.
"""

import asyncio
from pathlib import Path
from typing import Any

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)
from kohakuterrarium.utils.file_walk import iter_matching_files
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@register_builtin("glob")
class GlobTool(BaseTool):
    """
    Tool for finding files by pattern.

    Supports glob patterns like **/*.py
    """

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "glob"

    @property
    def description(self) -> str:
        return "Find files by glob pattern (sorted by modification time)"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Find files matching pattern."""
        context = kwargs.get("context")

        pattern = args.get("pattern", "")
        if not pattern:
            return ToolResult(error="No pattern provided")

        # Get base path
        base_path = args.get("path", ".")
        base = Path(base_path).expanduser().resolve()

        # Path boundary guard
        if context and context.path_guard:
            msg = context.path_guard.check(str(base))
            if msg:
                return ToolResult(error=msg)

        if not base.exists():
            return ToolResult(error=f"Path not found: {base_path}")

        # Get options
        limit = int(args.get("limit", 100))
        follow_gitignore = str(args.get("gitignore", "true")).lower() not in (
            "false",
            "no",
            "0",
        )

        try:
            # Run blocking walk/stat in thread pool
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
        """Synchronous file finding (runs in thread pool)."""
        # Collect files with a cap to avoid materialising enormous trees.
        # Cap at 10× the display limit (or 5 000), whichever is larger.
        cap = max(limit * 10, 5_000) if limit > 0 else 50_000

        matches: list[Path] = list(
            iter_matching_files(
                base,
                pattern,
                gitignore=follow_gitignore,
                cap=cap,
            )
        )
        hit_cap = len(matches) >= cap

        # Sort by modification time (newest first).
        # stat() only runs on the capped subset, not every file on disk.
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        # Apply display limit
        total = len(matches)
        if limit > 0 and total > limit:
            matches = matches[:limit]

        # Format output
        output_lines = []
        for match in matches:
            try:
                rel_path = match.relative_to(base)
            except ValueError:
                rel_path = match
            output_lines.append(str(rel_path))

        output = "\n".join(output_lines)

        if hit_cap:
            output += (
                f"\n\n... (showing {len(matches)} of {total} collected, "
                f"capped at {cap}; more may exist — narrow your pattern)"
            )
        elif total > len(matches):
            output += f"\n\n... ({total} total, showing {len(matches)})"

        logger.debug(
            "Glob search",
            pattern=pattern,
            matches=len(matches),
        )

        return ToolResult(output=output or "(no matches)", exit_code=0)
