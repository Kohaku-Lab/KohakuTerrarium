"""File editing through exact search/replace or unified diffs."""

import os
import re
import time
from dataclasses import dataclass
from difflib import unified_diff
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
from kohakuterrarium.utils.file_guard import check_read_before_write, is_binary_file
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

MAX_RESULT_DIFF_LINES = 200


@dataclass
class DiffHunk:
    """A single hunk from a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]  # Prefixes preserve context, removal, and addition semantics.


class DiffParseError(Exception):
    """Indicate malformed or inapplicable unified-diff input."""

    pass


def parse_unified_diff(diff_text: str) -> list[DiffHunk]:
    """Parse standard unified-diff text into validated hunk structures."""
    lines = diff_text.split("\n")
    hunks: list[DiffHunk] = []
    current_hunk: DiffHunk | None = None
    hunk_pattern = re.compile(r"^@@\s*-(\d+)(?:,(\d+))?\s*\+(\d+)(?:,(\d+))?\s*@@")

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.startswith("---") or line.startswith("+++"):
            i += 1
            continue

        match = hunk_pattern.match(line)
        if match:
            if current_hunk:
                hunks.append(current_hunk)

            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) else 1

            current_hunk = DiffHunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                lines=[],
            )
            i += 1
            continue

        if current_hunk is not None:
            if line.startswith(" ") or line.startswith("-") or line.startswith("+"):
                current_hunk.lines.append(line)
            elif line.startswith("\\"):
                # The marker describes file termination and is not hunk content.
                pass
            elif line == "":
                # ``split`` erases a context line's single-space prefix, so recover
                # it only while the hunk still expects content.
                expected = current_hunk.old_count + current_hunk.new_count
                actual_context = sum(
                    1
                    for l in current_hunk.lines
                    if l.startswith(" ") or l.startswith("-") or l.startswith("+")
                )
                if actual_context < expected:
                    current_hunk.lines.append(" ")
            i += 1
            continue

        i += 1

    if current_hunk:
        hunks.append(current_hunk)

    if not hunks:
        raise DiffParseError("No valid hunks found in diff")

    return hunks


def apply_hunks(original: str, hunks: list[DiffHunk]) -> str:
    """Apply parsed hunks, rejecting line-range or context mismatches."""
    original_lines = original.split("\n")
    # Splitting drops termination semantics, so preserve the original final newline.
    had_trailing_newline = original.endswith("\n")
    if had_trailing_newline and original_lines and original_lines[-1] == "":
        original_lines = original_lines[:-1]

    # Reverse application keeps earlier hunk coordinates stable.
    sorted_hunks = sorted(hunks, key=lambda h: h.old_start, reverse=True)

    for hunk in sorted_hunks:
        old_lines = []
        new_lines = []

        for line in hunk.lines:
            if line.startswith(" "):
                old_lines.append(line[1:])
                new_lines.append(line[1:])
            elif line.startswith("-"):
                old_lines.append(line[1:])
            elif line.startswith("+"):
                new_lines.append(line[1:])

        start_idx = hunk.old_start - 1

        if old_lines:
            end_idx = start_idx + len(old_lines)
            if end_idx > len(original_lines):
                raise DiffParseError(
                    f"Hunk at line {hunk.old_start} extends beyond file "
                    f"(file has {len(original_lines)} lines, hunk needs {end_idx})"
                )

            actual_lines = original_lines[start_idx:end_idx]

            for i, (expected, actual) in enumerate(zip(old_lines, actual_lines)):
                if expected != actual:
                    raise DiffParseError(
                        f"Context mismatch at line {hunk.old_start + i}:\n"
                        f"  Expected: {expected!r}\n"
                        f"  Actual:   {actual!r}"
                    )

            original_lines[start_idx:end_idx] = new_lines
        else:
            original_lines[start_idx:start_idx] = new_lines

    result = "\n".join(original_lines)
    if had_trailing_newline:
        result += "\n"

    return result


def check_edit_guards(file_path: Path, context: Any) -> ToolResult | None:
    """Return the first pre-edit guard failure, if any."""
    if is_binary_file(file_path):
        return ToolResult(
            error=f"Binary file detected ({file_path.suffix}). "
            "Use bash with xxd, file, or other tools to inspect binary files."
        )
    if context and context.path_guard:
        msg = context.path_guard.check(str(file_path))
        if msg:
            return ToolResult(error=msg)
    msg = check_read_before_write(
        context.file_read_state if context else None, str(file_path)
    )
    if msg:
        return ToolResult(error=msg)
    return None


def update_edit_read_state(file_path: Path, context: Any) -> None:
    """Update file read state after a successful edit."""
    if context and context.file_read_state:
        mtime_ns = os.stat(file_path).st_mtime_ns
        context.file_read_state.record_read(
            str(file_path), mtime_ns, False, time.time()
        )


def build_result_diff(path: Path, original: str, new_content: str) -> str:
    """Build a unified diff of the actual content change for tool output."""
    diff_lines = list(
        unified_diff(
            original.splitlines(),
            new_content.splitlines(),
            fromfile=f"a/{path.as_posix()}",
            tofile=f"b/{path.as_posix()}",
            lineterm="",
        )
    )
    if not diff_lines:
        return ""
    if len(diff_lines) > MAX_RESULT_DIFF_LINES:
        shown = "\n".join(diff_lines[:MAX_RESULT_DIFF_LINES])
        return (
            f"{shown}\n"
            f"... diff truncated ({len(diff_lines) - MAX_RESULT_DIFF_LINES} more lines)"
        )
    return "\n".join(diff_lines)


@register_builtin("edit")
class EditTool(BaseTool):
    """Edit one file through exact replacement or standard unified diffs."""

    needs_context = True
    # Serial execution prevents a concurrent writer from invalidating guard state.
    is_concurrency_safe = False

    @property
    def tool_name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return "Edit one file by unified diff, or by one search/replace. Use for patch application and text tool-call formats. Not for several edits - use multi_edit."

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Select and execute the edit mode encoded by the arguments."""
        context = kwargs.get("context")
        path = args.get("path", "")

        if not path:
            return ToolResult(
                error="No path provided. The edit tool supports two modes:\n\n"
                "1. Unified diff: path + diff\n"
                "2. Search/replace: path + old + new\n"
            )

        has_diff = bool(args.get("diff"))
        has_old = "old" in args

        if has_old:
            return await self._execute_search_replace(path, args, context)
        if has_diff:
            return await self._execute_unified_diff(path, args, context)

        return ToolResult(
            error="Missing edit content. Provide either:\n"
            "- diff: unified diff content, OR\n"
            "- old + new: search/replace"
        )

    def _check_guards(self, file_path: Path, context: Any) -> ToolResult | None:
        return check_edit_guards(file_path, context)

    def _update_read_state(self, file_path: Path, context: Any) -> None:
        update_edit_read_state(file_path, context)

    async def _execute_search_replace(
        self, path: str, args: dict[str, Any], context: Any
    ) -> ToolResult:
        """Search/replace mode: find old in file, replace with new."""
        old = args.get("old", "")
        new = args.get("new", "")
        replace_all = args.get("replace_all", False)

        if not old:
            return ToolResult(error="old is empty. Provide the exact text to find.")

        file_path = resolve_tool_path(path, context)

        guard = self._check_guards(file_path, context)
        if guard:
            return guard

        if not file_path.exists():
            return ToolResult(error=f"File not found: {path}")
        if not file_path.is_file():
            return ToolResult(error=f"Not a file: {path}")

        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                original = await f.read()

            count = original.count(old)
            if count == 0:
                return ToolResult(
                    error="old not found in file. "
                    "Make sure it matches the file content exactly "
                    "(including whitespace and indentation)."
                )

            if count > 1 and not replace_all:
                return ToolResult(
                    error=f"Found {count} occurrences of old. "
                    "Provide more surrounding context to uniquely identify "
                    "the target, or set replace_all=true to replace all."
                )

            if replace_all:
                new_content = original.replace(old, new)
            else:
                new_content = original.replace(old, new, 1)

            if new_content == original:
                return ToolResult(
                    output="No changes made (old equals new)",
                    exit_code=0,
                )

            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(new_content)

            replaced = count if replace_all else 1
            logger.debug(
                "File edited (search/replace)",
                file_path=str(file_path),
                replacements=replaced,
            )

            self._update_read_state(file_path, context)

            return ToolResult(
                output=(f"Edited {file_path}\n" f"  {replaced} replacement(s) made"),
                exit_code=0,
                metadata={
                    "canvas_preview": build_canvas_preview(
                        kind="edit",
                        file_path=str(file_path),
                        content=new_content,
                    ),
                },
            )

        except PermissionError:
            return ToolResult(error=f"Permission denied: {path}")
        except Exception as e:
            logger.error("Edit (search/replace) failed", error=str(e))
            return ToolResult(error=str(e))

    async def _execute_unified_diff(
        self, path: str, args: dict[str, Any], context: Any
    ) -> ToolResult:
        """Unified diff mode: apply hunks from a standard diff."""
        diff = args.get("diff", "")

        file_path = resolve_tool_path(path, context)

        guard = self._check_guards(file_path, context)
        if guard:
            return guard

        if not file_path.exists():
            return ToolResult(error=f"File not found: {path}")

        if not file_path.is_file():
            return ToolResult(error=f"Not a file: {path}")

        try:
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                original = await f.read()

            try:
                hunks = parse_unified_diff(diff)
            except DiffParseError as e:
                return ToolResult(
                    error=f"Invalid diff format: {e}\n\n"
                    "Unified diff format:\n"
                    "@@ -10,3 +10,4 @@\n"
                    " context line (starts with space)\n"
                    "-line to remove (starts with minus)\n"
                    "+line to add (starts with plus)\n"
                    "+another new line\n\n"
                    "IMPORTANT:\n"
                    "- @@ -N,M +N,M @@ is the hunk header (line numbers)\n"
                    "- Lines starting with space = context (unchanged)\n"
                    "- Lines starting with - = removed\n"
                    "- Lines starting with + = added"
                )

            try:
                new_content = apply_hunks(original, hunks)
            except DiffParseError as e:
                return ToolResult(
                    error=f"Failed to apply diff: {e}\n\n"
                    'TIP: Use <read path="file"/> first to see exact line '
                    "numbers and content, then match them exactly in your diff."
                )

            if new_content == original:
                return ToolResult(
                    output="No changes made (diff produced identical content)",
                    exit_code=0,
                )

            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(new_content)

            added = sum(1 for h in hunks for l in h.lines if l.startswith("+"))
            removed = sum(1 for h in hunks for l in h.lines if l.startswith("-"))

            logger.debug(
                "File edited",
                file_path=str(file_path),
                hunks=len(hunks),
                added=added,
                removed=removed,
            )

            self._update_read_state(file_path, context)

            return ToolResult(
                output=(
                    f"Edited {file_path}\n"
                    f"  {len(hunks)} hunk(s) applied\n"
                    f"  +{added} -{removed} lines"
                ),
                exit_code=0,
                metadata={
                    "canvas_preview": build_canvas_preview(
                        kind="edit",
                        file_path=str(file_path),
                        content=new_content,
                    ),
                },
            )

        except PermissionError:
            return ToolResult(error=f"Permission denied: {path}")
        except Exception as e:
            logger.error("Edit failed", error=str(e))
            return ToolResult(error=str(e))
