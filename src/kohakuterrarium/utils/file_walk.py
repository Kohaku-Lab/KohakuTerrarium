"""
Gitignore-aware file walking with early termination.

Provides directory/file iterators that respect ``.gitignore`` at every
level (scoped rules included, via
:class:kohakuterrarium.utils.file_ignore.GitIgnoreFilter) and skip
common build/cache directories immediately.  Used by the grep and glob
tools; the tree tool shares the skip/ignore helpers but walks with its
own recursive builder (per-directory sorting, depth limits and
truncation reporting).

Traversal is built on ``os.scandir``: one listing call per directory
supplies the name, directory/file type and — on Windows — the stat
data for every entry, so walking costs one syscall pass per directory
instead of one metadata query per entry.
"""

import os
import re
from os import scandir
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


def _walk(
    root: Path,
    *,
    gitignore: bool,
    show_hidden: bool,
    cap: int,
    _ignore: GitIgnoreFilter | None = None,
) -> Iterator[tuple[Path, str, os.DirEntry]]:
    """Core depth-first walk shared by the public walkers.

    Yields ``(path, relpath, entry)`` where *relpath* is the path
    relative to *root* with forward slashes and *entry* is the raw
    ``os.DirEntry`` — its cached ``stat()`` stays usable after the
    scandir listing closed, so callers can stat only the entries they
    actually keep.
    """
    ignore = (_ignore or GitIgnoreFilter(root)) if gitignore else None
    if ignore and ignore.is_ignored(root, True):
        return

    count = 0
    # Stack entries: (directory, relative posix dir)
    stack: list[tuple[Path, str]] = [(root, "")]

    while stack:
        current, rel_dir = stack.pop()

        try:
            entries = list(scandir(current))
        except (PermissionError, OSError):
            continue

        # One hoisted ignore-checker per directory; per entry it only
        # matches the entry name against precomputed rule prefixes.
        check = ignore.prepare_dir(current) if ignore else None

        subdirs: list[tuple[Path, str]] = []
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
            if check is not None and check(name, entry_is_dir):
                continue

            if entry_is_dir:
                subdirs.append((Path(entry.path), f"{rel_dir}{name}/"))
                continue

            yield Path(entry.path), f"{rel_dir}{name}", entry
            count += 1
            if cap and count >= cap:
                return

        # Reverse for stable DFS ordering (alphabetical-ish)
        stack.extend(reversed(subdirs))


def walk_files(
    root: Path,
    *,
    gitignore: bool = True,
    show_hidden: bool = False,
    cap: int = 0,
    _ignore: GitIgnoreFilter | None = None,
) -> Iterator[Path]:
    """Yield files under *root*, skipping ignored subtrees.

    Uses iterative DFS over ``os.scandir`` listings.  Unconditionally
    skips ``ALWAYS_SKIP_NAMES`` directories and respects scoped
    ``.gitignore`` rules through :class:`GitIgnoreFilter`.

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
    _ignore:
        Reuse an existing filter (rooted at the search base) instead of
        building one at *root*.  Internal; used by glob matching so
        rules from outside the narrowed walk root still apply.
    """
    for path, _rel, _entry in _walk(
        root,
        gitignore=gitignore,
        show_hidden=show_hidden,
        cap=cap,
        _ignore=_ignore,
    ):
        yield path


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

    stack: list[Path] = [root]

    while stack:
        current = stack.pop()
        yield current

        try:
            # Sorted by name for stable, alphabetical-ish DFS ordering.
            with scandir(current) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except (PermissionError, OSError):
            continue

        # One hoisted ignore-checker per directory (entries are subdirs).
        check = ignore.prepare_dir(current) if ignore else None

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
            if check is not None and check(name, True):
                continue
            subdirs.append(Path(entry.path))

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

    Recursive patterns use a single walk of the pattern's literal
    prefix subtree and match full base-relative paths, with either
    slash style accepted as a separator.  Non-recursive patterns
    delegate to ``Path.glob()`` directly.

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
    for path, _rel, _stat in _iter_matching(
        base, pattern, gitignore=gitignore, cap=cap, want_stat=False
    ):
        yield path


def iter_matching_files_stat(
    base: Path,
    pattern: str,
    *,
    gitignore: bool = True,
    cap: int = 0,
) -> Iterator[tuple[Path, "os.stat_result | None"]]:
    """Like :func:`iter_matching_files` but yields ``(path, stat)`` pairs.

    The stat comes from the same traversal that found the file (free on
    Windows, where scandir already returned the metadata), so callers
    can order or filter by mtime/size without re-stating every path.
    *stat* may be ``None`` in the rare case the metadata is unavailable
    mid-walk.
    """
    for path, _rel, stat in _iter_matching(
        base, pattern, gitignore=gitignore, cap=cap, want_stat=True
    ):
        yield path, stat


def _iter_matching(
    base: Path,
    pattern: str,
    *,
    gitignore: bool,
    cap: int,
    want_stat: bool,
) -> Iterator[tuple[Path, str, "os.stat_result | None"]]:
    """Shared implementation; yields ``(path, base-relative relpath, stat)``."""
    ignore = GitIgnoreFilter(base) if gitignore else None

    if "**" not in pattern:
        # Non-recursive — Path.glob is fast, no deep walking needed
        count = 0
        for f in base.glob(pattern):
            try:
                if not f.is_file():
                    continue
                if ignore and ignore.is_ignored(f, False):
                    continue
                stat = f.stat() if want_stat else None
            except (PermissionError, OSError):
                continue
            yield f, f.name, stat
            count += 1
            if cap and count >= cap:
                return
        return

    # Recursive pattern. Use the leading literal segment (everything
    # before the first "**/") only as a cheap walk-root narrowing, then
    # walk that subtree once and match each file's full *base-relative*
    # path against the COMPLETE pattern.
    #
    # Two correctness reasons for matching the whole pattern via
    # ``_walk`` rather than per-directory ``Path.glob(suffix)``:
    #   * matching the whole pattern (not the post-split suffix) is what
    #     makes leading and intermediate "**/" segments work — e.g.
    #     "**/c/**/*.py" must match "a/b/c/y.py" at any depth.
    #   * ``_walk`` filters ignored *files* against .gitignore, not
    #     just ignored directories — ``Path.glob`` would leak them.
    pattern = pattern.replace("\\", "/")
    parts = pattern.split("**/", 1)
    prefix = parts[0].rstrip("/").rstrip("\\")

    walk_root = base / prefix if prefix else base
    if not walk_root.is_dir():
        return

    rel_prefix = f"{prefix}/" if prefix else ""
    matcher = _glob_to_regex(pattern)
    count = 0
    # The walk already carries POSIX relpaths, so match candidates are
    # plain string concats — no per-file ``relative_to`` round-trip.
    # The filter is rooted at *base* so rules anchored outside the
    # narrowed walk root still apply.
    # Stats are taken only from matched entries: on Windows the
    # DirEntry serves them from the scandir cache (free), on POSIX it
    # costs one syscall per match — the same count as statching the
    # collected subset after a full traversal, without the extra pass.
    for _path, rel, entry in _walk(
        walk_root,
        gitignore=gitignore,
        show_hidden=False,
        cap=0,
        _ignore=ignore,
    ):
        rel = f"{rel_prefix}{rel}"
        if not matcher.match(rel):
            continue
        stat = None
        if want_stat:
            try:
                stat = entry.stat()
            except (PermissionError, OSError):
                stat = None
        yield _path, rel, stat
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
