"""Discover common Git Bash installation paths on Windows."""

import os
from pathlib import Path


def windows_git_bash_candidates() -> list[str]:
    """Return likely ``bash.exe`` paths in discovery priority order."""
    candidates: list[str] = []
    program_files = [
        os.environ.get("ProgramW6432"),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")

    for base in [p for p in program_files if p]:
        candidates.extend(
            [
                str(Path(base) / "Git" / "bin" / "bash.exe"),
                str(Path(base) / "Git" / "usr" / "bin" / "bash.exe"),
            ]
        )
    if local_app_data:
        candidates.append(
            str(Path(local_app_data) / "Programs" / "Git" / "bin" / "bash.exe")
        )
    if home:
        candidates.extend(
            [
                str(
                    Path(home)
                    / "AppData"
                    / "Local"
                    / "Programs"
                    / "Git"
                    / "bin"
                    / "bash.exe"
                ),
                str(
                    Path(home)
                    / "scoop"
                    / "apps"
                    / "git"
                    / "current"
                    / "bin"
                    / "bash.exe"
                ),
            ]
        )

    # Windows paths are case-insensitive, but discovery priority must be stable.
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


__all__ = ["windows_git_bash_candidates"]
