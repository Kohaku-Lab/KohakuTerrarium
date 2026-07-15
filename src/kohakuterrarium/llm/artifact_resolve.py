"""Resolve local session-artifact image URLs to inline ``data:`` URLs.

Inline local session artifacts at the provider boundary.

Stored conversations keep compact relative URLs, while outgoing requests use
base64 data URLs that external providers can consume.
"""

import base64
import re
from typing import Any

from kohakuterrarium.studio.persistence.artifacts import (
    resolve_artifact_file,
    resolve_artifacts_dir,
)
from kohakuterrarium.studio.persistence.store import _session_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_ARTIFACT_URL_RE = re.compile(r"^/api/sessions/(?P<sid>[^/]+)/artifacts/(?P<path>.+)$")

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


def resolve_artifact_url(url: str) -> str:
    """Inline a local artifact URL, preserving the original URL on failure."""
    if not isinstance(url, str) or not url.startswith("/api/sessions/"):
        return url
    match = _ARTIFACT_URL_RE.match(url)
    if not match:
        return url
    sid = match.group("sid")
    rel = match.group("path")
    try:
        artifacts = resolve_artifacts_dir(sid, _session_dir())
        path = resolve_artifact_file(artifacts, rel)
        data = path.read_bytes()
    except Exception as exc:
        logger.warning(
            "Artifact URL resolve failed — sending as-is",
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
