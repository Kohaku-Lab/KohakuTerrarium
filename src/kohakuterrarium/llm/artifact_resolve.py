"""Resolve local session-artifact image URLs to inline ``data:`` URLs.

Tool images are materialized by the controller to
``/api/sessions/{sid}/artifacts/{path}`` — a relative URL that points at
our own FastAPI server (keeps the stored conversation small). External
providers can't fetch a relative local path, so before sending we resolve
it back to a base64 ``data:`` URL read from disk.

This was originally bespoke to the Codex provider; it now lives here so
every provider (OpenAI, Anthropic, Codex) shares one implementation. The
provider boundary is the right place: the stored conversation / session
file keep the small ``/api/sessions/...`` URL, and only the outgoing
request carries the inlined bytes.
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
    """Resolve a relative ``/api/sessions/.../artifacts/...`` URL to a data URL.

    Returns the original URL untouched when it isn't a local artifact URL
    (already a ``data:`` URL, fully-qualified http(s), unparseable, or
    missing on disk). Failures are logged at warning level; the caller
    falls back to the original string and lets the provider surface any
    error.
    """
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
    """Resolve local artifact URLs inside every ``image_url`` content part.

    Walks Chat-Completions-shaped messages and rewrites the ``url`` of any
    ``image_url`` part that points at a local session artifact into a
    ``data:`` URL. Identity-preserving: returns the original list (and
    original message/part dicts) when nothing needed resolving, so the
    common "no images / already-data-URL" path stays a no-op.
    """
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
