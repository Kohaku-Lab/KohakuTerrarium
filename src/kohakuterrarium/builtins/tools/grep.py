"""Regex search over text files with gitignore-aware traversal."""

import os
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

_READ_BATCH_SIZE = 64 * 1024


@register_builtin("grep")
class GrepTool(BaseTool):
    """Search text files with a regular expression and optional glob filter."""

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return "Search file contents by regex. Use to find where something is defined or used. Not for finding files by name - use glob."

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
        limit_arg = args.get("limit", 50)
        if isinstance(limit_arg, bool) or not isinstance(limit_arg, (int, str)):
            return ToolResult(error="limit must be a positive integer")
        try:
            limit = int(limit_arg)
        except ValueError:
            return ToolResult(error="limit must be a positive integer")
        if limit <= 0:
            return ToolResult(error="limit must be a positive integer")
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

            # iter_matching_files yields non-directory entries; the
            # single-file branch checked base.is_file() above. On POSIX
            # that can still include FIFOs/sockets/devices, which would
            # block forever on open() — the baseline filtered them via
            # is_file(), keep that guard there. NTFS cannot hold them,
            # so Windows skips the stat entirely.
            for file_path in files_iter:
                if os.name == "posix" and not file_path.is_file():
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
    regex: re.Pattern[str],
    base: Path,
    remaining_limit: int,
) -> list[dict[str, Any]]:
    """Return line-oriented regex matches from one text file."""
    matches: list[dict[str, Any]] = []
    if remaining_limit <= 0:
        return matches
    try:
        try:
            rel_path = path.relative_to(base)
        except ValueError:
            rel_path = path
        display_path = str(rel_path)
        async with aiofiles.open(path, encoding="utf-8", errors="replace") as f:
            line_num = 0
            while lines := await f.readlines(_READ_BATCH_SIZE):
                for line in lines:
                    line_num += 1
                    if not regex.search(line):
                        continue

                    content = line.rstrip()
                    if len(content) > 2000:
                        content = content[:2000] + " ... (truncated)"

                    matches.append(
                        {
                            "file": display_path,
                            "line": line_num,
                            "content": content,
                        }
                    )
                    if len(matches) >= remaining_limit:
                        return matches
    except Exception as e:
        logger.warning("Failed to search file for matches", error=str(e), exc_info=True)
    return matches
