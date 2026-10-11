"""Apply request-wide Anthropic image dimension limits to outbound messages."""

import base64
import binascii
import io
from collections.abc import Iterator
from typing import Any

from PIL import Image, ImageOps

from kohakuterrarium.llm.image_preparation import body_bytes
from kohakuterrarium.llm.request_budget import RequestMeasure, fit_request

# Anthropic's request limit is 32 MB; the target leaves room for headers and tools.
ANTHROPIC_REQUEST_BYTES = 28_000_000

_MIME_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


def _media_blocks(content: Any) -> Iterator[dict[str, Any]]:
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"image", "document"}:
            yield block
        elif block.get("type") == "tool_result":
            yield from _media_blocks(block.get("content"))


def _resize_image(block: dict[str, Any], limit: int) -> dict[str, Any]:
    source = block.get("source")
    if not isinstance(source, dict) or source.get("type") != "base64":
        return block
    data = source.get("data")
    if not isinstance(data, str):
        return block
    try:
        raw = base64.b64decode(data, validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            # Camera MPO files are JPEGs whose first frame is the photo.
            format = "JPEG" if image.format == "MPO" else image.format
            if format not in _MIME_TYPES or max(image.size) <= limit:
                return block
            oriented = ImageOps.exif_transpose(image)
            if oriented.mode == "P" or "transparency" in oriented.info:
                oriented = oriented.convert("RGBA")
            elif oriented.mode == "1":
                oriented = oriented.convert("L")
            width, height = oriented.size
            scale = limit / max(width, height)
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            resized = oriented.resize(size, Image.Resampling.LANCZOS)
            output = io.BytesIO()
            options = {"quality": 95} if format in {"JPEG", "WEBP"} else {}
            resized.save(output, format=format, **options)
    except (binascii.Error, OSError, ValueError, Image.DecompressionBombError):
        return block
    return {
        **block,
        "source": {
            **source,
            "media_type": _MIME_TYPES[format],
            "data": base64.b64encode(output.getvalue()).decode("ascii"),
        },
    }


def _replace_content(content: Any, replacements: dict[int, dict[str, Any]]) -> Any:
    if not isinstance(content, list):
        return content
    result = []
    changed = False
    for block in content:
        replacement = replacements.get(id(block), block)
        if isinstance(block, dict) and block.get("type") == "tool_result":
            nested = _replace_content(block.get("content"), replacements)
            if nested is not block.get("content"):
                replacement = {**block, "content": nested}
        changed |= replacement is not block
        result.append(replacement)
    return result if changed else content


def prepare_anthropic_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resize oversized inline images in final wire messages; may run off-thread."""
    blocks = [block for msg in messages for block in _media_blocks(msg.get("content"))]
    limit = 2000 if len(blocks) > 20 else 8000
    replacements = {}
    for block in blocks:
        if block.get("type") == "image" and id(block) not in replacements:
            replacements[id(block)] = _resize_image(block, limit)
    result = []
    changed = False
    for msg in messages:
        content = _replace_content(msg.get("content"), replacements)
        if content is msg.get("content"):
            result.append(msg)
        else:
            changed = True
            result.append({**msg, "content": content})
    return result if changed else messages


def prepare_anthropic_request(
    kwargs: dict[str, Any], *, max_bytes: int | None = ANTHROPIC_REQUEST_BYTES
) -> tuple[dict[str, Any], RequestMeasure]:
    """Compress inline request images toward a whole-body target and measure the body.

    ``max_bytes`` of ``None`` only measures. Returns ``kwargs`` itself when
    nothing changed.
    """
    body = {
        key: value
        for key, value in kwargs.items()
        if key not in {"extra_body", "extra_headers", "extra_query", "timeout"}
    }
    body.update(kwargs.get("extra_body") or {})
    has_images = any(
        block.get("type") == "image"
        and isinstance(block.get("source"), dict)
        and block["source"].get("type") == "base64"
        and isinstance(block["source"].get("data"), str)
        for msg in body.get("messages", [])
        for block in _media_blocks(msg.get("content"))
    )
    if not has_images:
        return kwargs, RequestMeasure(body_bytes(body), 0)
    prepared, measure = fit_request(body, max_bytes, provider="anthropic")
    if prepared is body:
        return kwargs, measure
    result = {**kwargs, "messages": prepared["messages"]}
    if "messages" in (kwargs.get("extra_body") or {}):
        result["extra_body"] = {
            **kwargs["extra_body"],
            "messages": prepared["messages"],
        }
    return result, measure
