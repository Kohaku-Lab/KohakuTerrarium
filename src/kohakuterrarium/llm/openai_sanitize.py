"""Pre-send content-part sanitisation and request-shape diagnostics
Sanitize OpenAI-compatible message payloads at the provider boundary.

Strict compatible servers may reject an entire content part for unknown keys,
so internal display metadata remains in storage but is removed from the wire.
"""

from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Known content parts are restricted to the documented Chat Completions schema.
_OAI_PART_ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "text": frozenset({"type", "text"}),
    "image_url": frozenset({"type", "image_url"}),
}

# Nested image data likewise permits only the documented URL and detail fields.
_OAI_IMAGE_URL_ALLOWED_KEYS: frozenset[str] = frozenset({"url", "detail"})

# Provider-owned assistant fields that only matter for local persistence and
# must never reach a Chat Completions-compatible endpoint.
_INTERNAL_MESSAGE_KEYS = frozenset({"_kt_assistant_segments", "_kt_anthropic_content"})


def strip_internal_message_fields(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove message-level internal fields while preserving identity when unchanged."""
    out: list[dict[str, Any]] = []
    any_changed = False
    for msg in messages:
        if not any(key in msg for key in _INTERNAL_MESSAGE_KEYS):
            out.append(msg)
            continue
        cleaned = {
            key: value
            for key, value in msg.items()
            if key not in _INTERNAL_MESSAGE_KEYS
        }
        out.append(cleaned)
        any_changed = True
    return out if any_changed else messages


def strip_kt_extras(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove internal keys from known parts while preserving identity when unchanged."""
    out: list[dict[str, Any]] = []
    any_changed = False
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        new_content: list[Any] = []
        msg_changed = False
        for part in content:
            if not isinstance(part, dict):
                new_content.append(part)
                continue
            ptype = part.get("type")
            allowed = _OAI_PART_ALLOWED_KEYS.get(ptype)
            if allowed is None:
                new_content.append(part)
                continue
            iu = part.get("image_url")
            iu_extra = (
                set(iu) - _OAI_IMAGE_URL_ALLOWED_KEYS
                if isinstance(iu, dict)
                else frozenset()
            )
            if set(part) <= allowed and not iu_extra:
                new_content.append(part)
                continue
            cleaned = {k: v for k, v in part.items() if k in allowed}
            if ptype == "image_url" and isinstance(iu, dict):
                cleaned["image_url"] = {
                    k: v for k, v in iu.items() if k in _OAI_IMAGE_URL_ALLOWED_KEYS
                }
            new_content.append(cleaned)
            msg_changed = True
        if msg_changed:
            out.append({**msg, "content": new_content})
            any_changed = True
        else:
            out.append(msg)
    return out if any_changed else messages


def strip_surrogates(text: str) -> str:
    """Drop lone surrogate code points that cannot survive UTF-8 encoding."""
    return text.encode("utf-8", errors="ignore").decode("utf-8")


def log_request_shape(msg: str, model: str, messages: list[dict[str, Any]]) -> None:
    """Log request shape at INFO for multimodal payloads and DEBUG otherwise."""
    image_count = 0
    file_count = 0
    multimodal_msgs = 0
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        had_extra = False
        for p in content:
            if not isinstance(p, dict):
                continue
            ptype = p.get("type")
            if ptype == "image_url":
                image_count += 1
                had_extra = True
            elif ptype == "file":
                file_count += 1
                had_extra = True
        if had_extra:
            multimodal_msgs += 1

    if image_count or file_count:
        logger.info(
            msg,
            model=model,
            messages=len(messages),
            multimodal_messages=multimodal_msgs,
            image_parts=image_count,
            file_parts=file_count,
        )
    else:
        logger.debug(msg, model=model, messages=len(messages))
