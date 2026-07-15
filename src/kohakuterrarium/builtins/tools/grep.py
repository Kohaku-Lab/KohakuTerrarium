"""Regex search over text files with gitignore-aware traversal."""

import re
from pathlib import Path
from typing import Any

import aiofiles

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
    resolve_tool_path,
)
from kohakuterrarium.utils.file_guard import is_binary_file
from kohakuterrarium.utils.file_walk import iter_matching_files
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


@register_builtin("grep")
class GrepTool(BaseTool):
    """Search text files with a regular expression and optional glob filter."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return "Search file contents with regex pattern matching"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Search files for pattern."""
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

        file_pattern = args.get("glob", "**/*")
        limit = int(args.get("limit", 50))
        case_insensitive = args.get("ignore_case", False)
        follow_gitignore = str(args.get("gitignore", "true")).lower() not in (
            "false",
            "no",
            "0",
        )

        try:
            flags = re.IGNORECASE if case_insensitive else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(error=f"Invalid regex: {e}")

        try:
            matches: list[dict[str, Any]] = []
            total_matches = 0
            files_searched = 0
            hit_cap = False

            if base.is_file():
                files_iter = iter([base])
            else:
                files_iter = iter_matching_files(
                    base, file_pattern, gitignore=follow_gitignore
                )

            for file_path in files_iter:
                if not file_path.is_file():
                    continue

                if is_binary_file(file_path):
                    continue

                files_searched += 1

                file_matches = await _search_single_file(
                    file_path, regex, base, limit - len(matches)
                )
                for m in file_matches:
                    total_matches += 1
                    if len(matches) < limit:
                        matches.append(m)

                # The reported total becomes a lower bound at the cap, trading
                # exhaustive counts for bounded traversal on large repositories.
                if total_matches >= limit:
                    hit_cap = True
                    break

            output_lines = []
            for match in matches:
                output_lines.append(
                    f"{match['file']}:{match['line']}: {match['content']}"
                )

            output = "\n".join(output_lines)

            if hit_cap:
                output += (
                    f"\n\n(Showing {len(matches)} matches from "
                    f"{files_searched} files; more may exist. "
                    "Narrow your pattern or glob to refine.)"
                )
            else:
                output += f"\n\n({total_matches} matches in {files_searched} files)"

            logger.debug(
                "Grep search",
                pattern=pattern,
                matches=total_matches,
                files=files_searched,
            )

            return ToolResult(output=output or "(no matches)", exit_code=0)

        except Exception as e:
            logger.error("Grep failed", error=str(e))
            return ToolResult(error=str(e))


async def _search_single_file(
    path: Path,
    regex: "re.Pattern",
    base: Path,
    remaining_limit: int,
) -> list[dict[str, Any]]:
    """Return line-oriented regex matches from one text file."""
    matches: list[dict[str, Any]] = []
    try:
        async with aiofiles.open(path, encoding="utf-8", errors="replace") as f:
            line_num = 0
            async for line in f:
                line_num += 1
                if not regex.search(line):
                    continue

                # Individual lines are bounded independently of the result-count cap.
                content = line.rstrip()
                if len(content) > 2000:
                    content = content[:2000] + " ... (truncated)"

                try:
                    rel_path = path.relative_to(base)
                except ValueError:
                    rel_path = path

                matches.append(
                    {
                        "file": str(rel_path),
                        "line": line_num,
                        "content": content,
                    }
                )
    except Exception as e:
        logger.warning("Failed to search file for matches", error=str(e), exc_info=True)
    return matches
