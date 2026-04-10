"""
Gitignore-aware file walking with early termination.

Provides directory/file iterators that respect ``.gitignore`` at every
level and skip common build/cache directories immediately.  Used by
the tree, grep, and glob tools to avoid scanning huge ignored subtrees
(``node_modules``, ``.git``, ``__pycache__``, …).
"""

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterator

# ── always-skip dirs ─────────────────────────────────────────────────
# Directories unconditionally skipped regardless of .gitignore state.
# Kept as a frozenset for O(1) exact-name lookups.

ALWAYS_SKIP_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".tox",
        ".eggs",
        ".venv",
        "venv",
        ".cache",
    }
)


def should_skip_dir(name: str) -> bool:
    """Return True if *name* is an unconditionally-skipped directory."""
    if name in ALWAYS_SKIP_NAMES:
        return True
    # Glob-style patterns that can't go in the frozenset
    if name.endswith(".egg-info"):
        return True
    return False


# ── gitignore helpers ────────────────────────────────────────────────


def parse_gitignore(gitignore_path: Path) -> list[str]:
    """Read a ``.gitignore`` and return non-empty, non-comment patterns."""
    try:
        text = gitignore_path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, PermissionError):
        return []
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def is_ignored(name: str, is_dir: bool, patterns: list[str]) -> bool:
    """Check if *name* matches any gitignore-style pattern (simplified).

    Handles trailing ``/`` (dir-only) patterns.  Negation (``!``) is
    intentionally not supported — negated patterns are skipped.
    """
    for pat in patterns:
        if pat.startswith("!"):
            continue
        if pat.endswith("/"):
            if is_dir and fnmatch(name, pat.rstrip("/")):
                return True
            continue
        if fnmatch(name, pat):
            return True
    return False


# ── walkers ──────────────────────────────────────────────────────────


def walk_files(
    root: Path,
    *,
    gitignore: bool = True,
    show_hidden: bool = False,
    cap: int = 0,
) -> Iterator[Path]:
    """Yield files under *root*, skipping ignored subtrees.

    Uses iterative DFS.  Unconditionally skips ``ALWAYS_SKIP_NAMES``
    directories, optionally parses ``.gitignore`` at every level.

    Parameters
    ----------
    root:
        Starting directory.
    gitignore:
        Parse and respect ``.gitignore`` files (default ``True``).
    show_hidden:
        Include dot-files / dot-dirs (default ``False``).
    cap:
        Stop after yielding this many files (0 = unlimited).
    """
    count = 0
    # Stack entries: (directory, inherited gitignore patterns)
    stack: list[tuple[Path, list[str]]] = [(root, [])]

    while stack:
        current, parent_patterns = stack.pop()

        # Build patterns for this directory
        patterns = list(parent_patterns)
        if gitignore:
            gi = current / ".gitignore"
            if gi.is_file():
                patterns.extend(parse_gitignore(gi))

        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue

        subdirs: list[tuple[Path, list[str]]] = []
        for entry in entries:
            name = entry.name

            # Hidden check (before always-skip so .git is caught either way)
            if not show_hidden and name.startswith("."):
                continue

            # Unconditional skip
            if should_skip_dir(name):
                continue

            try:
                entry_is_dir = entry.is_dir()
            except (PermissionError, OSError):
                continue

            # Gitignore check
            if gitignore and is_ignored(name, entry_is_dir, patterns):
                continue

            if entry_is_dir:
                subdirs.append((entry, patterns))
            else:
                yield entry
                count += 1
                if cap and count >= cap:
                    return

        # Reverse for stable DFS ordering (alphabetical-ish)
        stack.extend(reversed(subdirs))


def walk_dirs(
    root: Path,
    *,
    gitignore: bool = True,
    show_hidden: bool = False,
) -> Iterator[Path]:
    """Yield directories under *root* (including *root* itself).

    Same filtering as :func:`walk_files` but yields directories instead
    of files.  Useful when the caller wants to run per-directory globs.
    """
    stack: list[tuple[Path, list[str]]] = [(root, [])]

    while stack:
        current, parent_patterns = stack.pop()
        yield current

        patterns = list(parent_patterns)
        if gitignore:
            gi = current / ".gitignore"
            if gi.is_file():
                patterns.extend(parse_gitignore(gi))

        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except (PermissionError, OSError):
            continue

        subdirs: list[tuple[Path, list[str]]] = []
        for entry in entries:
            name = entry.name
            if not show_hidden and name.startswith("."):
                continue
            if should_skip_dir(name):
                continue
            try:
                if not entry.is_dir():
                    continue
            except (PermissionError, OSError):
                continue
            if gitignore and is_ignored(name, True, patterns):
                continue
            subdirs.append((entry, patterns))

        stack.extend(reversed(subdirs))


# ── glob-aware file iteration ────────────────────────────────────────


def iter_matching_files(
    base: Path,
    pattern: str,
    *,
    gitignore: bool = True,
    cap: int = 0,
) -> Iterator[Path]:
    """Yield files matching a glob *pattern* under *base*.

    For recursive patterns (containing ``**``), uses :func:`walk_dirs`
    to skip ignored subtrees, then runs a per-directory non-recursive
    glob on the suffix.  Non-recursive patterns delegate to
    ``Path.glob()`` directly.

    Parameters
    ----------
    base:
        Root directory for the search.
    pattern:
        Glob pattern, e.g. ``**/*.py``, ``src/**/*.ts``, ``*.md``.
    gitignore:
        Respect ``.gitignore`` when walking (default ``True``).
    cap:
        Stop after yielding this many files (0 = unlimited).
    """
    if "**" not in pattern:
        # Non-recursive — Path.glob is fast, no deep walking needed
        count = 0
        for f in base.glob(pattern):
            try:
                if f.is_file():
                    yield f
                    count += 1
                    if cap and count >= cap:
                        return
            except (PermissionError, OSError):
                continue
        return

    # Split at the first "**/" boundary
    parts = pattern.split("**/", 1)
    prefix = parts[0].rstrip("/").rstrip("\\")
    suffix = parts[1] if len(parts) > 1 else "*"

    walk_root = base / prefix if prefix else base
    if not walk_root.is_dir():
        return

    # If suffix itself contains "**", fall back to walk_files + full match
    if "**" in suffix:
        count = 0
        for f in walk_files(walk_root, gitignore=gitignore, cap=cap):
            try:
                rel = f.relative_to(walk_root)
            except ValueError:
                continue
            rel_str = str(rel).replace("\\", "/")
            if _glob_match(rel_str, suffix):
                yield f
                count += 1
                if cap and count >= cap:
                    return
        return

    # Common case: walk dirs, per-dir non-recursive glob on suffix
    count = 0
    for dir_path in walk_dirs(walk_root, gitignore=gitignore):
        try:
            for f in dir_path.glob(suffix):
                if f.is_file():
                    yield f
                    count += 1
                    if cap and count >= cap:
                        return
        except (PermissionError, OSError):
            continue


# ── internal glob pattern matcher ────────────────────────────────────


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a glob pattern (with ``**`` support) to a compiled regex."""
    pattern = pattern.replace("\\", "/")
    result = ""
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # ** — match any path segments
                if i + 2 < n and pattern[i + 2] == "/":
                    result += "(?:.+/)?"
                    i += 3
                else:
                    result += ".*"
                    i += 2
            else:
                result += "[^/]*"
                i += 1
        elif c == "?":
            result += "[^/]"
            i += 1
        elif c in r".+^${}|()[]\\":
            result += "\\" + c
            i += 1
        else:
            result += c
            i += 1
    return re.compile(result + "$")


def _glob_match(path: str, pattern: str) -> bool:
    """Match a forward-slash relative path against a glob pattern."""
    return bool(_glob_to_regex(pattern).match(path))
