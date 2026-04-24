"""Auto-activate skills on user_input via ``paths:`` frontmatter.

Spec D.6 + Qd: on every user input, scan the cwd for files that match
any enabled skill's ``paths`` globs. For each match, inject a short
hint into the next ``pre_llm_call`` asking the model to consider the
explicit ``skill`` tool if the task matches.

Kept cheap (per spec): at most one cwd scan per user_input, cached
on ``(cwd, top-level mtime)`` so re-evaluating within the same turn
is a no-op.
"""

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kohakuterrarium.skills.registry import Skill, SkillRegistry
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


_DEFAULT_MAX_FILES_SCANNED = 500
_DEFAULT_MAX_DEPTH = 3


@dataclass(frozen=True)
class _CacheKey:
    cwd: str
    mtime: float


class SkillPathScanner:
    """Stateful helper — scans cwd once per (cwd, mtime) and caches the
    matching skill set."""

    def __init__(
        self,
        *,
        max_files: int = _DEFAULT_MAX_FILES_SCANNED,
        max_depth: int = _DEFAULT_MAX_DEPTH,
    ) -> None:
        self._max_files = max_files
        self._max_depth = max_depth
        self._cache: tuple[_CacheKey, list[str]] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def matching_skills(
        self,
        registry: SkillRegistry,
        cwd: Path,
    ) -> list[Skill]:
        """Return enabled skills whose ``paths`` match any file under cwd."""
        relevant = [s for s in registry.list_enabled() if s.paths]
        if not relevant:
            return []
        files = self._scan(cwd)
        matches: list[Skill] = []
        for skill in relevant:
            for pattern in skill.paths:
                if _any_match(files, pattern):
                    matches.append(skill)
                    break
        return matches

    def format_hint(self, matched: Iterable[Skill]) -> str:
        """Build the hint message for matched skills (one short paragraph)."""
        matched = [s for s in matched if not s.invocation_blocked]
        if not matched:
            return ""
        lines = ["## Skill Context", ""]
        lines.append(
            "The current working directory contains files matched by "
            "these skills' `paths` filters. Consider invoking the "
            "relevant one with the `skill` tool if your task matches."
        )
        for skill in matched:
            patterns = ", ".join(f"`{p}`" for p in skill.paths)
            desc = (skill.description or "").splitlines()[0][:200]
            lines.append(f"- **{skill.name}** — matches {patterns}. {desc}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _scan(self, cwd: Path) -> list[str]:
        if not cwd.exists() or not cwd.is_dir():
            return []
        try:
            mtime = cwd.stat().st_mtime
        except OSError:
            mtime = 0.0
        key = _CacheKey(cwd=str(cwd.resolve()), mtime=mtime)
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        files = _list_files(cwd, self._max_depth, self._max_files)
        self._cache = (key, files)
        return files


def _list_files(root: Path, max_depth: int, max_files: int) -> list[str]:
    """List repo-relative file paths, breadth-first, bounded.

    Skips hidden dirs and common heavy dirs (``node_modules``,
    ``.git``, ``venv``, etc.) to keep the scan cheap.
    """
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "dist",
        "build",
    }
    out: list[str] = []
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue and len(out) < max_files:
        current, depth = queue.pop(0)
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if len(out) >= max_files:
                break
            if entry.name.startswith("."):
                # Skip hidden but keep scanning the dotfile-free subtree root.
                if entry.is_dir() and entry.name in skip_dirs:
                    continue
                if entry.is_dir():
                    continue
            if entry.is_dir():
                if entry.name in skip_dirs:
                    continue
                if depth < max_depth:
                    queue.append((entry, depth + 1))
                continue
            if entry.is_file():
                try:
                    rel = entry.relative_to(root).as_posix()
                except ValueError:
                    rel = entry.name
                out.append(rel)
    return out


def _any_match(files: list[str], pattern: str) -> bool:
    """Return True if any file matches the glob pattern.

    Supports ``**`` recursive globs and basename-only matches.
    """
    pattern = pattern.strip()
    if not pattern:
        return False
    # fnmatch does not understand "**" natively; convert it to "*" so a
    # pattern like ``src/**/*.py`` matches ``src/foo/bar.py``.
    normalised = pattern.replace("**/", "").replace("/**", "")
    for path in files:
        if fnmatch.fnmatchcase(path, pattern):
            return True
        if fnmatch.fnmatchcase(path, normalised):
            return True
        # Basename match so patterns like "*.pdf" match "subdir/foo.pdf".
        base = path.rsplit("/", 1)[-1]
        if fnmatch.fnmatchcase(base, pattern):
            return True
    return False
