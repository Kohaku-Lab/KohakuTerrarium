"""Prepare local image references against explicit session paths or URL resolvers."""

import base64
import re
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from kohakuterrarium.errors import MediaReferenceError
from kohakuterrarium.session.artifacts import resolve_artifact_file
from kohakuterrarium.utils.fs_path import coerce_fs_path
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


_session_path: ContextVar[Path | None] = ContextVar("media_session_path", default=None)


@contextmanager
def session_media_scope(path: str | Path | None):
    """Bind artifact ownership to provider execution and its awaited worker tasks."""
    token = _session_path.set(Path(path) if path is not None else None)
    try:
        yield
    finally:
        _session_path.reset(token)


def current_media_session_path() -> Path | None:
    """Return the session location bound to this provider execution context."""
    return _session_path.get()


def artifact_reference(url: str) -> tuple[str, str] | None:
    """Decode an artifact URL into a safe namespace and relative filename."""
    match = _ARTIFACT_URL_RE.match(url)
    if not match:
        return None
    namespace = unquote(match.group("sid"))
    if namespace in {".", ".."} or any(
        separator in namespace for separator in ("/", "\\", ":")
    ):
        raise ValueError("Invalid artifact session namespace")
    return namespace, unquote(match.group("path"))


def file_reference_path(url: str) -> Path | None:
    """Return the path of a local file URL, excluding remote file authorities."""
    if not isinstance(url, str) or not url.startswith("file://"):
        return None
    try:
        if urlparse(url).netloc.lower() not in ("", "localhost"):
            return None
        return coerce_fs_path(url)
    except ValueError:
        return None


def session_media_path(url: str, *, session_path: Path) -> Path | None:
    """Resolve local file or artifact URLs within an explicit session root."""
    file_path = file_reference_path(url)
    if file_path is not None:
        return file_path
    reference = artifact_reference(url)
    if reference is None:
        return None
    namespace, filename = reference
    return resolve_artifact_file(
        session_path.parent / f"{namespace}.artifacts", filename
    )


def _image_mime(data: bytes) -> str | None:
    """Identify raster MIME types by signature without decoding pixels."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def resolve_media_url(url: str, path_resolver: Callable[[str], Path | None]) -> str:
    """Inline a local media URL, preserving the reference on resolution failure."""
    if not isinstance(url, str) or not url.startswith(("/api/sessions/", "file://")):
        return url
    try:
        path = path_resolver(url)
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
    mime = _image_mime(data) or _ARTIFACT_MIME_BY_EXT.get(
        path.suffix.lower(), "application/octet-stream"
    )
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def prepare_image_urls(
    messages: list[dict[str, Any]], resolver: Callable[[str], str]
) -> list[dict[str, Any]]:
    """Prepare effective image blocks without modifying historical references."""
    any_changed = False
    out = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        new_content = []
        msg_changed = False
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "image_url"
                and isinstance(part.get("image_url"), (dict, str))
            ):
                image = part["image_url"]
                url = image.get("url") if isinstance(image, dict) else image
                resolved = resolver(url) if isinstance(url, str) else url
                if isinstance(resolved, str) and resolved.startswith(
                    ("file://", "/api/sessions/")
                ):
                    raise MediaReferenceError(
                        f"Required local media is unavailable: {url}. "
                        "Restore the file or supply the image again. For missing "
                        "images in completed older turns, inspect /repair-images."
                    )
                if resolved is not url and resolved != url:
                    new_content.append(
                        {
                            **part,
                            "image_url": (
                                {**image, "url": resolved}
                                if isinstance(image, dict)
                                else resolved
                            ),
                        }
                    )
                    msg_changed = True
                    continue
            new_content.append(part)
        out.append({**msg, "content": new_content} if msg_changed else msg)
        any_changed |= msg_changed
    return out if any_changed else messages


def resolve_session_image_urls(
    messages: list[dict], *, session_path: str | Path
) -> list[dict]:
    """Prepare request images using an explicit session's filesystem location."""
    paths = partial(session_media_path, session_path=Path(session_path))
    return prepare_image_urls(messages, partial(resolve_media_url, path_resolver=paths))
