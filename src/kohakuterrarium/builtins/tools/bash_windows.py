"""Windows-only ``bash.exe`` discovery for the ``bash`` tool.

Split out of ``bash.py`` to keep that file under the 600-line cap
(it grew when the mobile-sandbox PATH-prepend block landed).  This
module is import-safe on every platform; the only Windows-specific
thing is the candidate-path list, which produces an empty result
on non-Windows shells.
"""

import os
from pathlib import Path


def windows_git_bash_candidates() -> list[str]:
    """Enumerate well-known ``bash.exe`` install locations on
    Windows — Git for Windows (Program Files / scoop / per-user),
    ordered most-likely-first.  Returns ``[]`` on non-Windows
    platforms.  Caller is responsible for ``Path.exists()`` checks;
    the candidate set is independent of what's installed.
    """
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

    # Dedupe case-insensitively while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


__all__ = ["windows_git_bash_candidates"]
