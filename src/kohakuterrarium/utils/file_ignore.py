"""Directory-scoped .gitignore matching for a single filesystem traversal."""

import os
from pathlib import Path

from pathspec.patterns.gitignore import GitIgnorePatternError
from pathspec.patterns.gitignore.spec import GitIgnoreSpecPattern


def _compile_rules(lines: list[str]) -> tuple[GitIgnoreSpecPattern, ...]:
    patterns = []
    for line in lines:
        if os.name == "nt":
            line = line.lower()
        if line.rstrip().endswith("/**/"):
            line = line.rstrip()[:-1] + "/*/"
        try:
            patterns.append(GitIgnoreSpecPattern(line))
        except GitIgnorePatternError:
            continue
    return tuple(patterns)


class GitIgnoreFilter:
    """Cache scoped rules and excluded parents within one search (blocking I/O)."""

    def __init__(self, root: Path):
        root = Path(os.path.abspath(root))
        self.boundary = root
        for directory in (root, *root.parents):
            if (directory / ".git").exists():
                self.boundary = directory
                break
        self._contexts: dict[
            Path, tuple[tuple[Path, tuple[GitIgnoreSpecPattern, ...]], ...]
        ] = {}
        self._excluded: dict[Path, bool] = {self.boundary: False}

    def _context(
        self, directory: Path
    ) -> tuple[tuple[Path, tuple[GitIgnoreSpecPattern, ...]], ...]:
        pending = []
        current = directory
        while current not in self._contexts:
            pending.append(current)
            if current == self.boundary:
                break
            current = current.parent
        context = self._contexts.get(current, ())
        for current in reversed(pending):
            try:
                lines = (
                    (current / ".gitignore")
                    .read_text(encoding="utf-8", errors="replace")
                    .splitlines()
                )
            except OSError:
                lines = []
            if lines:
                context = (*context, (current, _compile_rules(lines)))
            self._contexts[current] = context
        return context

    def _matches(self, path: Path, is_dir: bool) -> bool:
        ignored = False
        for directory, patterns in self._context(path.parent):
            relative = path.relative_to(directory).as_posix()
            if os.name == "nt":
                relative = relative.lower()
            for pattern in reversed(patterns):
                # Parent matches are evaluated separately by _directory_ignored.
                candidate = relative
                if is_dir and pattern.pattern.rstrip().endswith("/"):
                    candidate += "/"
                if pattern.regex and any(
                    match.lastgroup is None or match.end() == len(candidate)
                    for match in pattern.regex.finditer(candidate)
                ):
                    ignored = bool(pattern.include)
                    break
        return ignored

    def _directory_ignored(self, directory: Path) -> bool:
        pending = []
        current = directory
        while current not in self._excluded:
            pending.append(current)
            current = current.parent
        ignored = self._excluded[current]
        for current in reversed(pending):
            ignored = ignored or self._matches(current, True)
            self._excluded[current] = ignored
        return ignored

    def is_ignored(self, path: Path, is_dir: bool) -> bool:
        """Match a path, preserving exclusion of any parent directory."""
        path = Path(os.path.abspath(path))
        if path == self.boundary or not path.is_relative_to(self.boundary):
            return False
        if is_dir:
            return self._directory_ignored(path)
        return self._directory_ignored(path.parent) or self._matches(path, False)
