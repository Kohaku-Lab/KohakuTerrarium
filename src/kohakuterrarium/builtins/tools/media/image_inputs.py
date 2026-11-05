"""Input images for ``image_gen`` edits: local files and recent conversation images."""

import base64
import io
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from kohakuterrarium.llm.media.artifacts import resolve_artifact_url

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_INPUT_SIDE = 2048
_MIME = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


class ImageInputError(ValueError):
    """An input image that cannot be sent; the message is for the model."""


def file_data_url(path: Path) -> str:
    """A ``data:`` URL for a local image, downscaled to ``MAX_INPUT_SIDE``."""
    if not path.is_file():
        raise ImageInputError(f"image not found: {path}")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ImageInputError(
            f"image too large (max {MAX_INPUT_BYTES // (1024 * 1024)}MB): {path}"
        )
    data = path.read_bytes()
    try:
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format
            image.load()
            if fmt not in _MIME:
                raise ImageInputError(f"unsupported image format {fmt}: {path}")
            if max(image.size) > MAX_INPUT_SIDE:
                scale = MAX_INPUT_SIDE / max(image.size)
                resized = image.convert(
                    "RGBA" if image.has_transparency_data else "RGB"
                )
                resized = resized.resize(
                    (
                        max(1, round(image.width * scale)),
                        max(1, round(image.height * scale)),
                    ),
                    Image.Resampling.LANCZOS,
                )
                buffer = io.BytesIO()
                resized.save(buffer, format="PNG")
                data, fmt = buffer.getvalue(), "PNG"
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ImageInputError(f"not a readable image: {path}") from exc
    return f"data:{_MIME[fmt]};base64,{base64.b64encode(data).decode('ascii')}"


def recent_image_urls(messages: Iterable[Any], count: int) -> list[str]:
    """The last ``count`` conversation images as inline URLs, newest first.

    Looks through user messages and tool results; a reference that cannot be
    inlined (a deleted artifact) is an error rather than a silent skip.
    """
    found: list[str] = []
    for message in reversed(list(messages)):
        content = message.to_dict().get("content")
        if not isinstance(content, list):
            continue
        for part in reversed(content):
            url = _image_url(part)
            if url is None:
                continue
            found.append(_inline(url))
            if len(found) == count:
                return found
    raise ImageInputError(
        f"the conversation holds only {len(found)} image(s); "
        f"{count} requested - pass referenced_image_paths or ask the user to attach them"
    )


def _image_url(part: Any) -> str | None:
    if not isinstance(part, dict) or part.get("type") != "image_url":
        return None
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    return url if isinstance(url, str) and url else None


def _inline(url: str) -> str:
    resolved = resolve_artifact_url(url)
    if resolved.startswith(("data:image/", "https://")):
        return resolved
    raise ImageInputError(f"a conversation image could not be loaded: {url}")
