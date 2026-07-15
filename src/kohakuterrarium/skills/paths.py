"""Match skill path globs against a bounded, cached working-tree scan."""

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
    """Cache bounded file scans by working directory and top-level mtime."""

    def __init__(
        self,
        *,
        max_files: int = _DEFAULT_MAX_FILES_SCANNED,
        max_depth: int = _DEFAULT_MAX_DEPTH,
    ) -> None:
        self._max_files = max_files
        self._max_depth = max_depth
        self._cache: tuple[_CacheKey, list[str]] | None = None

    def matching_skills(
        self,
        registry: SkillRegistry,
        cwd: Path,
    ) -> list[Skill]:
        """Return enabled skills whose path globs match a scanned file."""
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
        """Format a concise hint for matched, model-invocable skills."""
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
            desc_lines = (skill.description or "").splitlines()
            desc = desc_lines[0][:200] if desc_lines else ""
            lines.append(f"- **{skill.name}** — matches {patterns}. {desc}")
        return "\n".join(lines)

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
    """List relative files breadth-first while bounding depth and count."""
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
    """Return whether a path, normalized recursive glob, or basename matches."""
    pattern = pattern.strip()
    if not pattern:
        return False
    # ``fnmatch`` lacks recursive-glob semantics, so test a normalized form too.
    normalised = pattern.replace("**/", "").replace("/**", "")
    for path in files:
        if fnmatch.fnmatchcase(path, pattern):
            return True
        if fnmatch.fnmatchcase(path, normalised):
            return True
        base = path.rsplit("/", 1)[-1]
        if fnmatch.fnmatchcase(base, pattern):
            return True
    return False
