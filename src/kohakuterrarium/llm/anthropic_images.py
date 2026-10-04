"""Apply request-wide Anthropic image dimension limits to outbound messages."""

import base64
import binascii
import io
import json
from collections.abc import Iterator
from typing import Any

from PIL import Image, ImageOps

from kohakuterrarium.llm.image_preparation import ImagePreparer
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)
_IMAGE_PREPARER = ImagePreparer()
_REQUEST_BYTES = 28_000_000

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
            format = image.format
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
    kwargs: dict[str, Any], *, max_bytes: int = _REQUEST_BYTES
) -> dict[str, Any]:
    """Compress inline request images with router policy and a whole-body target."""
    body = {
        key: value
        for key, value in kwargs.items()
        if key not in {"extra_body", "extra_headers", "extra_query", "timeout"}
    }
    body.update(kwargs.get("extra_body") or {})
    blocks = [
        block
        for msg in body.get("messages", [])
        for block in _media_blocks(msg.get("content"))
        if block.get("type") == "image"
        and isinstance(block.get("source"), dict)
        and block["source"].get("type") == "base64"
        and isinstance(block["source"].get("data"), str)
    ]
    if not blocks:
        return kwargs
    initial_size = len(
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    )
    encoded_bytes = sum(len(block["source"]["data"]) for block in blocks)
    available = max_bytes - (initial_size - encoded_bytes)
    target = 512_000
    if initial_size > max_bytes and available > 0:
        estimate = int(available * 0.9 * 3 / 4 / len(blocks))
        target = max(1024, min(target, estimate // 1024 * 1024))
    prepared, stats = _IMAGE_PREPARER.prepare(body, max_image_bytes=target)
    size = len(json.dumps(prepared, ensure_ascii=False, separators=(",", ":")).encode())
    for _ in range(3):
        if size <= max_bytes or available <= 0 or target <= 1024:
            break
        target = max(1024, target // 2 // 1024 * 1024)
        prepared, stats = _IMAGE_PREPARER.prepare(body, max_image_bytes=target)
        size = len(
            json.dumps(prepared, ensure_ascii=False, separators=(",", ":")).encode()
        )
    logger.debug(
        "Anthropic image preparation",
        image_count=stats.image_count,
        cache_hits=stats.cache_hits,
        initial_request_bytes=initial_size,
        prepared_request_bytes=size,
        image_byte_target=target,
    )
    if size > max_bytes:
        logger.warning(
            "Anthropic request remains above image preparation target",
            request_bytes=size,
            target_bytes=max_bytes,
        )
    result = {**kwargs, "messages": prepared["messages"]}
    if "messages" in (kwargs.get("extra_body") or {}):
        result["extra_body"] = {
            **kwargs["extra_body"],
            "messages": prepared["messages"],
        }
    return result
