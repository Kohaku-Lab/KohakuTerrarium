"""
Edit tool - modify files via unified diff or search/replace.

Supports two modes (auto-detected from arguments):
- **Unified diff**: ``path`` + ``diff`` args. Multi-hunk, context-aware.
- **Search/replace**: ``path`` + ``old`` + ``new`` args.
  Simple single-string replacement with optional ``replace_all``.
"""

import os
import re
import time
from dataclasses import dataclass
from difflib import unified_diff
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
    lines: list[str]  # Lines with prefixes: ' ', '-', '+'


class DiffParseError(Exception):
    """Error parsing diff format."""

    pass


def parse_unified_diff(diff_text: str) -> list[DiffHunk]:
    """
    Parse unified diff format into hunks.

    Supports standard unified diff:
    - Lines starting with '-' are removed
    - Lines starting with '+' are added
    - Lines starting with ' ' are context (unchanged)
    - @@ -old_start,old_count +new_start,new_count @@ markers

    Args:
        diff_text: The unified diff content

    Returns:
        List of DiffHunk objects

    Raises:
        DiffParseError: If diff format is invalid
    """
    lines = diff_text.split("\n")
    hunks: list[DiffHunk] = []
    current_hunk: DiffHunk | None = None
    hunk_pattern = re.compile(r"^@@\s*-(\d+)(?:,(\d+))?\s*\+(\d+)(?:,(\d+))?\s*@@")

    i = 0
    while i < len(lines):
        line = lines[i]

        # Skip file headers (--- and +++ lines)
        if line.startswith("---") or line.startswith("+++"):
            i += 1
            continue

        # Check for hunk header
        match = hunk_pattern.match(line)
        if match:
            # Save previous hunk
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

        # If we're in a hunk, collect lines
        if current_hunk is not None:
            if line.startswith(" ") or line.startswith("-") or line.startswith("+"):
                current_hunk.lines.append(line)
            elif line.startswith("\\"):
                # "\ No newline at end of file" - skip
                pass
            elif line == "":
                # Empty line could be context (space was stripped) or end of hunk
                # Treat as context if we haven't reached expected line count
                expected = current_hunk.old_count + current_hunk.new_count
                actual_context = sum(
                    1
                    for l in current_hunk.lines
                    if l.startswith(" ") or l.startswith("-") or l.startswith("+")
                )
                if actual_context < expected:
                    current_hunk.lines.append(" ")  # Treat as context
            i += 1
            continue

        i += 1

    # Save last hunk
    if current_hunk:
        hunks.append(current_hunk)

    if not hunks:
        raise DiffParseError("No valid hunks found in diff")

    return hunks


def apply_hunks(original: str, hunks: list[DiffHunk]) -> str:
    """
    Apply diff hunks to original content.

    Args:
        original: Original file content
        hunks: List of parsed hunks to apply

    Returns:
        Modified content

    Raises:
        DiffParseError: If hunk cannot be applied (context mismatch)
    """
    original_lines = original.split("\n")
    # Track if original ended with newline
    had_trailing_newline = original.endswith("\n")
    if had_trailing_newline and original_lines and original_lines[-1] == "":
        original_lines = original_lines[:-1]

    # Apply hunks in reverse order to preserve line numbers
    sorted_hunks = sorted(hunks, key=lambda h: h.old_start, reverse=True)

    for hunk in sorted_hunks:
        # Extract expected old lines and new lines from hunk
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

        # Find where to apply (0-indexed)
        start_idx = hunk.old_start - 1

        # Verify context matches (if we have old lines to match)
        if old_lines:
            end_idx = start_idx + len(old_lines)
            if end_idx > len(original_lines):
                raise DiffParseError(
                    f"Hunk at line {hunk.old_start} extends beyond file "
                    f"(file has {len(original_lines)} lines, hunk needs {end_idx})"
                )

            actual_lines = original_lines[start_idx:end_idx]

            # Check for context match
            for i, (expected, actual) in enumerate(zip(old_lines, actual_lines)):
                if expected != actual:
                    raise DiffParseError(
                        f"Context mismatch at line {hunk.old_start + i}:\n"
                        f"  Expected: {expected!r}\n"
                        f"  Actual:   {actual!r}"
                    )

            # Apply: remove old, insert new
            original_lines[start_idx:end_idx] = new_lines
        else:
            # Pure insertion (no old lines)
            original_lines[start_idx:start_idx] = new_lines

    result = "\n".join(original_lines)
    if had_trailing_newline:
        result += "\n"

    return result


def check_edit_guards(file_path: Path, context: Any) -> ToolResult | None:
    """Run all pre-edit guards. Returns ToolResult on failure, None on success."""
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
    """
    Tool for editing files using unified diff format.

    Accepts standard unified diff with:
    - @@ -start,count +start,count @@ hunk headers
    - Lines starting with '-' for deletions
    - Lines starting with '+' for additions
    - Lines starting with ' ' for context
    """

    needs_context = True
    require_manual_read = True

    @property
    def tool_name(self) -> str:
        return "edit"

    @property
    def description(self) -> str:
        return (
            "Edit file via search/replace (path, old, new) or unified diff (path, diff). "
            "Prefer multi_edit for multiple same-file search/replace edits. Use info(edit) first."
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """Edit a file using unified diff or search/replace.

        Mode is auto-detected from arguments:
        - ``diff`` present: unified diff mode
        - ``old`` present: search/replace mode
        """
        context = kwargs.get("context")
        path = args.get("path", "")

        if not path:
            return ToolResult(
                error="No path provided. The edit tool supports two modes:\n\n"
                "1. Unified diff: path + diff\n"
                "2. Search/replace: path + old + new\n"
            )

        # Detect mode from args
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
            # Read current content
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                original = await f.read()

            # Parse diff
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

            # Apply hunks
            try:
                new_content = apply_hunks(original, hunks)
            except DiffParseError as e:
                return ToolResult(
                    error=f"Failed to apply diff: {e}\n\n"
                    'TIP: Use <read path="file"/> first to see exact line '
                    "numbers and content, then match them exactly in your diff."
                )

            # Check if anything changed
            if new_content == original:
                return ToolResult(
                    output="No changes made (diff produced identical content)",
                    exit_code=0,
                )

            # Write back
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(new_content)

            # Calculate stats
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
            )

        except PermissionError:
            return ToolResult(error=f"Permission denied: {path}")
        except Exception as e:
            logger.error("Edit failed", error=str(e))
            return ToolResult(error=str(e))
