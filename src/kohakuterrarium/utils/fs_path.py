"""Turn user-supplied path strings (including ``file://`` URLs) into Paths."""

from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname


def coerce_fs_path(value: str | Path) -> Path:
    """Return a filesystem Path for a plain path or a local ``file://`` URL.

    Remote ``file://host/...`` strings raise ``ValueError`` so callers cannot
    ``mkdir`` a folder named ``file:``.
    """
    text = value if isinstance(value, str) else str(value)
    if not text:
        raise ValueError("path must be non-empty")
    if text.startswith("file:"):
        return _path_from_file_uri(text)
    return Path(text).expanduser()


def _path_from_file_uri(text: str) -> Path:
    uri = text if text.startswith("file://") else "file://" + text[len("file:") :]
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise ValueError(f"unsupported file URI: {text!r}")
    path = url2pathname(parsed.path)
    if not path:
        raise ValueError(f"unsupported file URI: {text!r}")
    return Path(path)
