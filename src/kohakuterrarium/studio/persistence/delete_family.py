"""Namespace-atomic staging and removal of session SQLite file families."""

import time
from pathlib import Path
from uuid import uuid4

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Windows releases a SQLite mapping a moment after ``close()`` returns; a rename
# in that window fails with WinError 32 even though nothing holds the file.
_RENAME_ATTEMPTS = 4
_RENAME_BACKOFF_S = 0.05


def _replace_with_retry(source: Path, destination: Path) -> None:
    """Rename ``source`` aside, retrying briefly while the OS releases handles."""
    for attempt in range(_RENAME_ATTEMPTS):
        try:
            source.replace(destination)
            return
        except OSError:
            if attempt == _RENAME_ATTEMPTS - 1:
                raise
            time.sleep(_RENAME_BACKOFF_S * (attempt + 1))


def detach_file_family(paths: list[Path]) -> list[tuple[Path, Path]]:
    """Rename every existing path aside, restoring all names if one rename fails."""
    detached: list[tuple[Path, Path]] = []
    try:
        for source in paths:
            if not source.exists():
                continue
            quarantine = source.with_name(f"{source.name}.orphan-{uuid4().hex}")
            _replace_with_retry(source, quarantine)
            detached.append((source, quarantine))
    except OSError:
        for source, quarantine in reversed(detached):
            if quarantine.exists():
                quarantine.replace(source)
        raise
    return detached


def remove_detached_family(detached, unlink) -> list[Path]:
    """Remove detached files, retaining undeletable ones under quarantine names."""
    neutralized: list[Path] = []
    for original, quarantine in detached:
        try:
            unlink(quarantine)
            neutralized.append(original)
        except OSError:
            logger.warning(
                "Detached session file remains quarantined",
                quarantine=str(quarantine),
            )
            neutralized.append(quarantine)
    return neutralized


__all__ = ["detach_file_family", "remove_detached_family"]
