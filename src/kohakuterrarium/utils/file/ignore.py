"""Directory-scoped .gitignore matching for a single filesystem traversal."""

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from pathspec.patterns.gitignore import GitIgnorePatternError
from pathspec.patterns.gitignore.spec import GitIgnoreSpecPattern


class _Rule(NamedTuple):
    """A compiled pattern with the flags ``_matches`` would otherwise
    recompute for every candidate."""

    include: bool
    regex: re.Pattern[str] | None
    # Segment-built pathspec regexes start with ^, where finditer could
    # only ever yield the position-0 match — one regex.match() is then
    # equivalent to scanning and fails fast. A few shortcut forms
    # (e.g. a bare "*" or trailing "/**/") compile unanchored and take
    # the finditer fallback below. False falls back to the scan.
    anchored: bool
    dir_only: bool  # pattern ends with "/" (directory-only rule)


def _compile_rules(lines: list[str]) -> tuple[_Rule, ...]:
    """Compile ignore *lines* into rules, in reverse line order.

    ``_matches`` scans last-line-first (the last matching gitignore
    rule wins); reversing here keeps the hot loop allocation-free.
    """
    rules: list[_Rule] = []
    for line in lines:
        if os.name == "nt":
            line = line.lower()
        if line.rstrip().endswith("/**/"):
            line = line.rstrip()[:-1] + "/*/"
        try:
            pattern = GitIgnoreSpecPattern(line)
        except GitIgnorePatternError:
            continue
        regex = pattern.regex
        rules.append(
            _Rule(
                include=bool(pattern.include),
                regex=regex if regex else None,
                anchored=bool(regex) and regex.pattern.startswith("^"),
                dir_only=pattern.pattern.rstrip().endswith("/"),
            )
        )
    return tuple(reversed(rules))


def _entry_prefix(full: str, base: str) -> str | None:
    """Prefix turning entry names of *full* into candidates relative to
    *base*, or ``None`` when the string prefix does not hold (caller
    falls back to pathlib).

    Mirrors ``Path.relative_to(base).as_posix()``: on POSIX a ``\\`` is
    an ordinary filename character and must survive untouched — only
    the Windows separator is normalized.
    """
    if full == base:
        return ""
    if full.startswith(base) and full[len(base)] in "\\/":
        prefix = full[len(base) + 1 :]
        if os.name == "nt":
            prefix = prefix.replace("\\", "/")
        return prefix + "/"
    return None


class GitIgnoreFilter:
    """Cache scoped rules and excluded parents within one search (blocking I/O)."""

    def __init__(self, root: Path):
        root = Path(os.path.abspath(root))
        self.boundary = root
        for directory in (root, *root.parents):
            if (directory / ".git").exists():
                self.boundary = directory
                break
        self._contexts: dict[Path, tuple[tuple[Path, tuple[_Rule, ...]], ...]] = {}
        self._excluded: dict[Path, bool] = {self.boundary: False}

    def _context(self, directory: Path) -> tuple[tuple[Path, tuple[_Rule, ...]], ...]:
        pending = []
        current = directory
        while current not in self._contexts:
            pending.append(current)
            # current == current.parent at a filesystem root: stop there
            # too, or an out-of-boundary directory would climb forever.
            if current == self.boundary or current == current.parent:
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
        for directory, rules in self._context(path.parent):
            relative = path.relative_to(directory).as_posix()
            if os.name == "nt":
                relative = relative.lower()
            for rule in rules:
                # Parent matches are evaluated separately by _directory_ignored.
                candidate = relative
                if is_dir and rule.dir_only:
                    candidate += "/"
                regex = rule.regex
                if regex is None:
                    continue
                if rule.anchored:
                    # ^-anchored: the scan below could only ever yield
                    # the position-0 match, so match() is equivalent
                    # and rejects most candidates on their first char.
                    match = regex.match(candidate)
                    if match is None or (
                        match.lastgroup is not None and match.end() != len(candidate)
                    ):
                        continue
                elif not any(  # unanchored: keep the original full scan
                    match.lastgroup is None or match.end() == len(candidate)
                    for match in regex.finditer(candidate)
                ):
                    continue
                ignored = rule.include
                break
        return ignored

    def _directory_ignored(self, directory: Path) -> bool:
        pending = []
        current = directory
        while current not in self._excluded and current != current.parent:
            pending.append(current)
            current = current.parent
        ignored = self._excluded.get(current, False)
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

    def prepare_dir(self, directory: Path) -> Callable[[str, bool], bool]:
        """Build a fast checker for direct entries of *directory*.

        ``is_ignored`` re-derives the rule context, the ancestor
        exclusion chain and the relative path for every single entry.
        A walker visiting all entries of one directory can hoist that
        work out of its loop::

            check = ignore.prepare_dir(current)
            ...
            if check(entry.name, entry_is_dir):
                continue

        The returned callable is bound to *directory* — entries must be
        its direct children — and its verdicts are identical to
        ``is_ignored(directory / name, is_dir)``.  Directories outside
        the boundary check as not ignored, like ``is_ignored`` does.
        """
        directory = Path(os.path.abspath(directory))
        if not directory.is_relative_to(self.boundary):
            # is_ignored returns False for everything outside the
            # boundary; hand back the same verdict without touching the
            # exclusion chain — climbing from here would never reach it.
            return lambda name, is_dir: False
        parent_excluded = self._directory_ignored(directory)
        context = self._context(directory)

        # Per rules-directory relative prefixes for entries of
        # *directory*: "" for the directory's own rules, its path under
        # each ancestor otherwise.  Computed once per directory.
        prefixes: list[tuple[tuple[_Rule, ...], str | None, Path]] = []
        for rules_dir, rules in context:
            prefixes.append(
                (rules, _entry_prefix(str(directory), str(rules_dir)), rules_dir)
            )

        def check(name: str, is_dir: bool) -> bool:
            if parent_excluded:
                return True
            ignored = False
            for rules, prefix, rules_dir in prefixes:
                if prefix is None:
                    relative = (directory / name).relative_to(rules_dir).as_posix()
                else:
                    relative = prefix + name
                if os.name == "nt":
                    relative = relative.lower()
                for rule in rules:
                    candidate = relative
                    if is_dir and rule.dir_only:
                        candidate += "/"
                    regex = rule.regex
                    if regex is None:
                        continue
                    if rule.anchored:
                        match = regex.match(candidate)
                        if match is None or (
                            match.lastgroup is not None
                            and match.end() != len(candidate)
                        ):
                            continue
                    elif not any(
                        match.lastgroup is None or match.end() == len(candidate)
                        for match in regex.finditer(candidate)
                    ):
                        continue
                    ignored = rule.include
                    break
            return ignored

        return check
