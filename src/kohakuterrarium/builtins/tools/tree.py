"""
Tree tool - list files with frontmatter summaries.

Shows directory structure and extracts summary from YAML frontmatter.
Respects .gitignore by default and limits output to avoid flooding context.
"""

import re
from pathlib import Path
from typing import Any

import aiofiles

from kohakuterrarium.builtins.tools.registry import register_builtin
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolResult,
)
from kohakuterrarium.utils.file_walk import (
    is_ignored,
    parse_gitignore,
    should_skip_dir,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Regex to extract YAML frontmatter
FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)


def parse_frontmatter(content: str) -> dict[str, Any]:
    """
    Parse YAML frontmatter from markdown content.

    Simple parser that handles common cases without full YAML dependency.
    """
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}

    frontmatter = {}
    yaml_content = match.group(1)

    for line in yaml_content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Simple key: value parsing
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            # Handle quoted strings
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]

            # Handle arrays [item1, item2]
            if value.startswith("[") and value.endswith("]"):
                items = value[1:-1].split(",")
                value = [item.strip().strip("\"'") for item in items if item.strip()]

            # Handle booleans
            if value in ("true", "True", "yes", "Yes"):
                value = True
            elif value in ("false", "False", "no", "No"):
                value = False

            frontmatter[key] = value

    return frontmatter


class _TreeBuilder:
    """Stateful tree builder with line limit and gitignore support."""

    def __init__(
        self,
        root: Path,
        max_depth: int,
        limit: int,
        show_hidden: bool,
        follow_gitignore: bool,
    ):
        self.root = root
        self.max_depth = max_depth
        self.limit = limit
        self.show_hidden = show_hidden
        self.follow_gitignore = follow_gitignore
        self.lines: list[str] = []
        self.truncated = False
        self.total_skipped = 0
        # Collect gitignore patterns per directory (inherited + local)
        self._ignore_stack: list[list[str]] = [[]]
        self.ignored_dirs: list[str] = []

    def _current_patterns(self) -> list[str]:
        """Flat list of all active ignore patterns."""
        result: list[str] = []
        for patterns in self._ignore_stack:
            result.extend(patterns)
        return result

    def _add_line(self, line: str) -> bool:
        """Append a line. Returns False if limit reached."""
        if self.limit > 0 and len(self.lines) >= self.limit:
            self.truncated = True
            return False
        self.lines.append(line)
        return True

    async def build(
        self,
        path: Path,
        prefix: str = "",
        depth: int = 0,
    ) -> None:
        if depth >= self.max_depth or self.truncated:
            return

        # Load .gitignore at this level
        local_patterns: list[str] = []
        if self.follow_gitignore:
            gi = path / ".gitignore"
            if gi.is_file():
                local_patterns = parse_gitignore(gi)
        self._ignore_stack.append(local_patterns)

        try:
            entries = sorted(
                path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except PermissionError:
            self._add_line(f"{prefix}(permission denied)")
            self._ignore_stack.pop()
            return

        # Filter hidden
        if not self.show_hidden:
            entries = [e for e in entries if not e.name.startswith(".")]

        # Filter unconditionally-skipped directories + gitignore patterns
        patterns = self._current_patterns() if self.follow_gitignore else []
        filtered = []
        for e in entries:
            if should_skip_dir(e.name):
                if e.is_dir():
                    self.ignored_dirs.append(e.name)
                self.total_skipped += 1
                continue
            if self.follow_gitignore and is_ignored(e.name, e.is_dir(), patterns):
                if e.is_dir():
                    self.ignored_dirs.append(e.name)
                self.total_skipped += 1
                continue
            filtered.append(e)
        entries = filtered

        for i, entry in enumerate(entries):
            if self.truncated:
                break
            is_last = i == len(entries) - 1
            connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
            child_prefix = prefix + ("    " if is_last else "\u2502   ")

            if entry.is_dir():
                if not self._add_line(f"{prefix}{connector}{entry.name}/"):
                    break
                await self.build(entry, child_prefix, depth + 1)
            else:
                summary = ""
                if entry.suffix in (".md", ".markdown"):
                    try:
                        async with aiofiles.open(
                            entry, encoding="utf-8", errors="ignore"
                        ) as f:
                            content = await f.read()
                        fm = parse_frontmatter(content)
                        if fm.get("summary"):
                            summary = f" - {fm['summary']}"
                        elif fm.get("title"):
                            summary = f" - {fm['title']}"
                        elif fm.get("description"):
                            summary = f" - {fm['description']}"
                        if fm.get("protected"):
                            summary = f" [protected]{summary}"
                    except Exception:
                        pass

                if not self._add_line(f"{prefix}{connector}{entry.name}{summary}"):
                    break

        self._ignore_stack.pop()


@register_builtin("tree")
class TreeTool(BaseTool):
    """
    Tool for listing directory structure with frontmatter summaries.

    Respects .gitignore and limits output to avoid flooding context.
    """

    needs_context = True

    @property
    def tool_name(self) -> str:
        return "tree"

    @property
    def description(self) -> str:
        return (
            "List files in tree format (respects .gitignore, max 100 lines by default)"
        )

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    def get_parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path"},
                "depth": {"type": "integer", "description": "Max depth (default 3)"},
                "limit": {
                    "type": "integer",
                    "description": "Max output lines (default 100, 0 = unlimited)",
                },
                "gitignore": {
                    "type": "boolean",
                    "description": "Follow .gitignore rules (default true)",
                },
            },
        }

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        """List directory tree with frontmatter summaries."""
        context = kwargs.get("context")

        path_str = args.get("path") or args.get("_body", ".").strip() or "."
        path = Path(path_str).expanduser().resolve()

        # Path boundary guard
        if context and context.path_guard:
            msg = context.path_guard.check(str(path))
            if msg:
                return ToolResult(error=msg)

        if not path.exists():
            return ToolResult(error=f"Path not found: {path_str}")

        if not path.is_dir():
            return ToolResult(error=f"Not a directory: {path_str}")

        max_depth = int(args.get("depth", 3))
        limit = int(args.get("limit", 100))
        show_hidden = str(args.get("hidden", "false")).lower() in ("true", "yes", "1")
        follow_gitignore = str(args.get("gitignore", "true")).lower() not in (
            "false",
            "no",
            "0",
        )

        try:
            builder = _TreeBuilder(
                root=path,
                max_depth=max_depth,
                limit=limit,
                show_hidden=show_hidden,
                follow_gitignore=follow_gitignore,
            )
            builder._add_line(f"{path.name}/")
            await builder.build(path)

            output = "\n".join(builder.lines)

            # Append footer with useful info
            footer_parts: list[str] = []
            if builder.truncated:
                footer_parts.append(
                    f"... (output limited to {limit} lines, "
                    f"use limit=N or path=subdir to see more)"
                )
            if builder.total_skipped > 0:
                ignored_preview = ", ".join(sorted(set(builder.ignored_dirs))[:5])
                footer_parts.append(
                    f"({builder.total_skipped} entries ignored by .gitignore"
                    + (f": {ignored_preview}..." if ignored_preview else "")
                    + ", use gitignore=false to show all)"
                )
            if footer_parts:
                output += "\n" + "\n".join(footer_parts)

            logger.debug(
                "Tree listing",
                path=str(path),
                depth=max_depth,
                lines=len(builder.lines),
                skipped=builder.total_skipped,
            )

            return ToolResult(output=output, exit_code=0)

        except Exception as e:
            logger.error("Tree failed", error=str(e))
            return ToolResult(error=str(e))
