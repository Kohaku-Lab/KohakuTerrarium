"""Turn user-supplied path strings (including ``file://`` URLs) into Paths."""

import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname


def coerce_fs_path(value: str | Path) -> Path:
    """Return a filesystem Path for a plain path or a local ``file://`` URL.

    Remote ``file://host/...`` strings raise ``ValueError`` on POSIX so
    callers cannot ``mkdir`` a folder named ``file:``. On Windows those
    URLs are UNC paths (``\\\\host\\share\\...``).
    """
    text = value if isinstance(value, str) else str(value)
    if not text:
        raise ValueError("path must be non-empty")
    if text.startswith("file:"):
        return _path_from_file_uri(text)
    return Path(text).expanduser()


def env_fs_path_text(raw: str) -> str:
    """Keep a plain env path verbatim; parse only ``file:`` URIs.

    ``Path('/custom')`` on Windows becomes ``\\custom``, which breaks
    callers and tests that store POSIX env overrides as strings.
    """
    if raw.startswith("file:"):
        return str(coerce_fs_path(raw))
    return raw


def _path_from_file_uri(text: str) -> Path:
    rest = text[5:]
    if rest.startswith("\\"):
        return _from_windows_pathlib_form(rest)
    uri = text if text.startswith("file://") else "file://" + rest
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"unsupported file URI: {text!r}")
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        if os.name != "nt":
            raise ValueError(f"unsupported file URI: {text!r}")
        share = url2pathname(parsed.path)
        return Path(f"\\\\{parsed.netloc}{share}")
    path = url2pathname(parsed.path)
    if not path:
        raise ValueError(f"unsupported file URI: {text!r}")
    if len(path) >= 3 and path[0] in "/\\" and path[1].isalpha() and path[2] == ":":
        path = path[1:]
    return Path(path)


def _from_windows_pathlib_form(rest: str) -> Path:
    """Recover ``file:\\C:\\Users\\...`` from ``str(Path(as_uri()))`` on Windows."""
    normalized = rest.replace("\\", "/")
    if len(rest) >= 3 and rest[1].isalpha() and rest[2] == ":":
        return _path_from_file_uri("file://" + normalized)
    return _path_from_file_uri("file://" + normalized.lstrip("/"))
