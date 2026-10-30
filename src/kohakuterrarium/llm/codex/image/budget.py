"""Bound Codex request images without changing persistent conversation history."""

import re
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_MAX_IMAGES = 50
_LIMIT_ERROR = re.compile(
    r"Exceeded maximum number of images\s*\((\d+)\)\s*allowed in the request",
    re.IGNORECASE,
)


def initial_image_limit(api_key: str | None, base_url: str) -> int | None:
    """Apply the default cap only to the known Codex OAuth endpoint."""
    if not api_key and base_url.rstrip("/") == CODEX_BASE_URL:
        return CODEX_MAX_IMAGES
    return None


def reported_image_limit(
    error: BaseException, current_limit: int | None = None
) -> int | None:
    """Extract a lower image limit from an explicit, unstarted server rejection."""
    if (
        getattr(error, "status_code", None) != 400
        or getattr(error, "mid_stream", False)
        or getattr(error, "transport", False)
    ):
        return None
    match = _LIMIT_ERROR.search(str(error))
    if match is None:
        return None
    try:
        limit = int(match[1])
    except ValueError:
        return None
    if limit <= 0 or (current_limit is not None and limit >= current_limit):
        return None
    return limit


def _is_image(part: Any) -> bool:
    if not isinstance(part, dict) or part.get("type") != "image_url":
        return False
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    return isinstance(url, str) and bool(url)


def limit_codex_images(
    messages: list[dict[str, Any]], max_images: int | None = CODEX_MAX_IMAGES
) -> list[dict[str, Any]]:
    """Keep the newest image references and mark omitted images as unseen."""
    if max_images is None:
        return messages
    counts = [
        (
            sum(_is_image(part) for part in message["content"])
            if message.get("role") in {"user", "tool"}
            and isinstance(message.get("content"), list)
            else 0
        )
        for message in messages
    ]
    total = sum(counts)
    remaining = total - max_images
    if remaining <= 0:
        return messages
    projected = []
    for message, count in zip(messages, counts):
        omitted = min(remaining, count)
        if not omitted:
            projected.append(message)
            continue
        remaining -= omitted
        to_drop = omitted
        parts = []
        for part in message["content"]:
            if to_drop and _is_image(part):
                if to_drop == omitted:
                    parts.append(
                        {
                            "type": "text",
                            "text": (
                                f"[{omitted} image(s) omitted from this request; "
                                f"only the newest {max_images} images are included. "
                                "The omitted images were not seen in this request. "
                                "Read them again in smaller batches if needed.]"
                            ),
                        }
                    )
                to_drop -= 1
            else:
                parts.append(part)
        projected.append({**message, "content": parts})
    logger.info(
        "Codex request image limit applied",
        image_parts=total,
        kept_images=max_images,
        omitted_images=total - max_images,
    )
    return projected
