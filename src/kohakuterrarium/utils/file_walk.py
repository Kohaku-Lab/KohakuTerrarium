"""
Gitignore-aware file walking with early termination.

Provides directory/file iterators that respect ``.gitignore`` at every
level and skip common build/cache directories immediately.  Used by
the tree, grep, and glob tools to avoid scanning huge ignored subtrees
(``node_modules``, ``.git``, ``__pycache__``, …).
"""

import re
from pathlib import Path
from typing import Iterator

from kohakuterrarium.utils.file_ignore import GitIgnoreFilter

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


# ── walkers ──────────────────────────────────────────────────────────


def walk_files(
    root: Path,
    *,
    gitignore: bool = True,
    show_hidden: bool = False,
    cap: int = 0,
    _ignore: GitIgnoreFilter | None = None,
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
    ignore = (_ignore or GitIgnoreFilter(root)) if gitignore else None
    if ignore and ignore.is_ignored(root, True):
        return
    stack = [root]

    while stack:
        current = stack.pop()

        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue

        subdirs: list[Path] = []
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
            if ignore and ignore.is_ignored(entry, entry_is_dir):
                continue

            if entry_is_dir:
                subdirs.append(entry)
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
    ignore = GitIgnoreFilter(root) if gitignore else None
    if ignore and ignore.is_ignored(root, True):
        return
    stack = [root]

    while stack:
        current = stack.pop()
        yield current

        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except (PermissionError, OSError):
            continue

        subdirs: list[Path] = []
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
            if ignore and ignore.is_ignored(entry, True):
                continue
            subdirs.append(entry)

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

    Recursive patterns use :func:`walk_files` and match full relative
    paths, with either slash style accepted as a separator. Non-recursive
    patterns delegate to ``Path.glob()`` directly.

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
    ignore = GitIgnoreFilter(base) if gitignore else None
    if "**" not in pattern:
        # Non-recursive — Path.glob is fast, no deep walking needed
        count = 0
        for f in base.glob(pattern):
            try:
                if f.is_file() and not (ignore and ignore.is_ignored(f, False)):
                    yield f
                    count += 1
                    if cap and count >= cap:
                        return
            except (PermissionError, OSError):
                continue
        return

    # Recursive pattern. Use the leading literal segment (everything
    # before the first "**/") only as a cheap walk-root narrowing, then
    # walk that subtree once with ``walk_files`` and match each file's
    # full *base-relative* path against the COMPLETE pattern.
    #
    # Two correctness reasons for matching the whole pattern via
    # ``walk_files`` rather than per-directory ``Path.glob(suffix)``:
    #   * matching the whole pattern (not the post-split suffix) is what
    #     makes leading and intermediate "**/" segments work — e.g.
    #     "**/c/**/*.py" must match "a/b/c/y.py" at any depth.
    #   * ``walk_files`` filters ignored *files* against .gitignore, not
    #     just ignored directories — ``Path.glob`` would leak them.
    pattern = pattern.replace("\\", "/")
    parts = pattern.split("**/", 1)
    prefix = parts[0].rstrip("/").rstrip("\\")

    walk_root = base / prefix if prefix else base
    if not walk_root.is_dir():
        return

    matcher = _glob_to_regex(pattern)
    count = 0
    for f in walk_files(walk_root, gitignore=gitignore, _ignore=ignore):
        try:
            rel = f.relative_to(base)
        except ValueError:
            continue
        rel_str = str(rel).replace("\\", "/")
        if matcher.match(rel_str):
            yield f
            count += 1
            if cap and count >= cap:
                return


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
