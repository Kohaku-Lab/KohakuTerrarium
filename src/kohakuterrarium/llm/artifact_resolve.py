"""Resolve local media references to inline ``data:`` URLs at the provider boundary.

Stored conversations keep compact references, a session-artifact URL for media
the session produced or a ``file://`` URL for a file a tool looked at, while
outgoing requests carry base64 data URLs that external providers can consume.
"""

import base64
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from kohakuterrarium.studio.persistence.artifacts import (
    resolve_artifact_file,
    resolve_artifacts_dir,
)
from kohakuterrarium.studio.persistence.store import _session_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_ARTIFACT_URL_RE = re.compile(r"^/api/sessions/(?P<sid>[^/]+)/artifacts/(?P<path>.+)$")
_FILE_SCHEME = "file://"

_ARTIFACT_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".svg": "image/svg+xml",
    ".heif": "image/heif",
    ".heic": "image/heic",
    ".avif": "image/avif",
}


def file_reference_path(url: str) -> Path | None:
    """Return the local path a ``file://`` reference names, or ``None``."""
    if not isinstance(url, str) or not url.startswith(_FILE_SCHEME):
        return None
    parsed = urlparse(url)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    return Path(url2pathname(unquote(parsed.path)))


def _local_media_path(url: str) -> Path | None:
    """Map a stored media reference to the file behind it."""
    file_path = file_reference_path(url)
    if file_path is not None:
        return file_path
    match = _ARTIFACT_URL_RE.match(url)
    if not match:
        return None
    artifacts = resolve_artifacts_dir(match.group("sid"), _session_dir())
    return resolve_artifact_file(artifacts, match.group("path"))


def resolve_artifact_url(url: str) -> str:
    """Inline a local media reference, preserving the original URL on failure."""
    if not isinstance(url, str):
        return url
    if not (url.startswith("/api/sessions/") or url.startswith(_FILE_SCHEME)):
        return url
    try:
        path = _local_media_path(url)
        if path is None:
            return url
        data = path.read_bytes()
    except Exception as exc:
        logger.warning(
            "Media reference resolve failed — sending as-is",
            url=url,
            error=str(exc),
            exc_info=True,
        )
        return url
    ext = path.suffix.lower()
    mime = _ARTIFACT_MIME_BY_EXT.get(ext, "application/octet-stream")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def resolve_message_image_urls(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Inline local image parts while preserving object identity when unchanged."""
    any_changed = False
    out: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        new_content: list[Any] = []
        msg_changed = False
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "image_url"
                and isinstance(part.get("image_url"), dict)
            ):
                iu = part["image_url"]
                url = iu.get("url")
                resolved = resolve_artifact_url(url) if isinstance(url, str) else url
                if resolved is not url and resolved != url:
                    new_content.append({**part, "image_url": {**iu, "url": resolved}})
                    msg_changed = True
                    continue
            new_content.append(part)
        if msg_changed:
            out.append({**msg, "content": new_content})
            any_changed = True
        else:
            out.append(msg)
    return out if any_changed else messages
