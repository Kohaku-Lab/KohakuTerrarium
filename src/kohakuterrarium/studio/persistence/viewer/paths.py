"""Shared session-file path resolution for the persistence routes.

Centralizes logical-session naming and file resolution across unversioned,
versioned, legacy-short, and mirrored session files. Every helper accepts an
explicit directory and prefers the highest available format version.
"""

import os
from pathlib import Path


def is_session_file_name(name: str) -> bool:
    """Return whether a file name has a supported session-file form."""
    return name.endswith((".kohakutr", ".kt")) or ".kohakutr.v" in name


def normalize_session_stem(path: Path) -> str:
    """Return the logical session name without format or legacy suffixes."""
    name = path.name
    if name.endswith(".kohakutr"):
        return name[: -len(".kohakutr")]
    if name.endswith(".kt"):
        return name[: -len(".kt")]
    if ".kohakutr.v" in name:
        idx = name.find(".kohakutr.v")
        return name[:idx]
    return path.stem


def all_session_files(session_dir: Path) -> list[Path]:
    """Return supported session files from the main and mirror directories.

    Lab-host mirrors are included so listing and history can surface worker
    sessions with their recorded node IDs. A directory that exists but cannot
    be scanned raises ``OSError`` rather than reading as empty.
    """
    if not session_dir.exists():
        return []
    scan_dirs = [session_dir]
    mirror_dir = session_dir / "mirror"
    if mirror_dir.is_dir():
        scan_dirs.append(mirror_dir)
    found: list[Path] = []
    for d in scan_dirs:
        with os.scandir(d) as entries:
            found.extend(
                Path(entry.path)
                for entry in entries
                if is_session_file_name(entry.name)
            )
    return found


def _version_rank(path: Path) -> int:
    """Numeric version of a versioned session file (``foo.kohakutr.v2`` → 2)."""
    name = path.name
    if ".kohakutr.v" in name:
        tail = name.rsplit(".v", 1)[-1]
        return int(tail) if tail.isdigit() else 0
    return 0


def resolve_session_path(session_name: str, session_dir: Path) -> Path | None:
    """Resolve a logical session name, preferring its highest version.

    Exact versioned and legacy names take precedence over unique normalized or
    fuzzy matches. Unversioned files remain available as rollback companions.
    """
    if not session_dir.exists():
        return None

    versions = sorted(
        (
            (int(p.name.rsplit(".v", 1)[1]), p)
            for p in session_dir.glob(f"{session_name}.kohakutr.v*")
            if p.name.rsplit(".v", 1)[-1].isdigit()
        ),
        reverse=True,
    )
    if versions:
        return versions[0][1]

    for ext in (".kohakutr", ".kt"):
        candidate = session_dir / f"{session_name}{ext}"
        if candidate.exists():
            return candidate

    matches = [
        p
        for p in all_session_files(session_dir)
        if normalize_session_stem(p) == session_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return max(matches, key=_version_rank)

    fuzzy = [
        p
        for p in all_session_files(session_dir)
        if normalize_session_stem(p).startswith(session_name)
        or session_name in normalize_session_stem(p)
    ]
    if len(fuzzy) == 1:
        return fuzzy[0]
    return None


def all_versions_for_session(session_name: str, session_dir: Path) -> list[Path]:
    """Return every version and legacy file for one logical session."""
    return [
        p
        for p in all_session_files(session_dir)
        if normalize_session_stem(p) == session_name
    ]


def pick_canonical_per_session(session_dir: Path) -> list[Path]:
    """Return the highest-versioned path for each logical session."""
    by_canonical: dict[str, Path] = {}
    for path in all_session_files(session_dir):
        key = normalize_session_stem(path)
        existing = by_canonical.get(key)
        if existing is None or _version_rank(path) > _version_rank(existing):
            by_canonical[key] = path
    return list(by_canonical.values())
