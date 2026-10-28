"""Session format detection and fail-closed writable admission (blocking I/O)."""

import os
import re
from pathlib import Path

from kohakuterrarium.session.readonly import read_strict_session_meta
from kohakuterrarium.session.resource_gate import ResourceReleaseError
from kohakuterrarium.utils.fs_path import coerce_fs_path

FORMAT_VERSION: int = 2


class SessionFormatError(ValueError):
    """A session format is unreadable, invalid, or unsupported for writing."""


def detect_format_version(path: str | Path) -> int:
    """Return a positive format version; only an absent marker means legacy v1."""
    p = coerce_fs_path(path)
    p.stat()
    try:
        meta = read_strict_session_meta(p)
        value = meta.get("format_version", 1)
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
            value = int(value)
        if type(value) is not int or value < 1:
            raise ValueError(f"Invalid format_version: {value!r}")
        return value
    except ResourceReleaseError:
        raise
    except Exception as exc:
        raise SessionFormatError(f"Cannot read session format at {p}: {exc}") from exc


def archive_family(path: str | Path) -> list[tuple[int | None, Path]]:
    """Return exact bare and numeric-suffixed archive companions."""
    source = coerce_fs_path(path)
    name = os.path.normcase(source.name)
    bare = source.with_name(re.sub(r"\.v[0-9]+$", "", name))
    pattern = re.compile(re.escape(bare.name) + r"(?:\.v([0-9]+))?$")
    try:
        entries = list(bare.parent.iterdir())
    except FileNotFoundError:
        return []
    result = []
    for candidate in entries:
        match = pattern.fullmatch(os.path.normcase(candidate.name))
        if match:
            suffix = int(match[1]) if match[1] is not None else None
            result.append((suffix, candidate))
    return result


def require_writable_format(path: str | Path) -> None:
    """Reject unreadable archives and future members of their archive family."""
    source = coerce_fs_path(path).absolute()
    try:
        source.stat()
    except FileNotFoundError:
        if source.is_symlink():
            raise SessionFormatError(f"Missing session format target: {source}")
    else:
        _require_supported(source, detect_format_version(source))
    families = dict.fromkeys(archive_family(source) + archive_family(source.resolve()))
    for suffix, candidate in families:
        if suffix is not None:
            _require_supported(candidate, suffix)
        if candidate != source:
            _require_supported(candidate, detect_format_version(candidate))


def _require_supported(path: Path, version: int) -> None:
    if version > FORMAT_VERSION:
        raise SessionFormatError(
            f"Session format v{version} at {path} exceeds supported "
            f"format v{FORMAT_VERSION}; upgrade before writing this archive family"
        )
