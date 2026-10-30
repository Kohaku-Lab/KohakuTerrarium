"""
Apply Anthropic's four-breakpoint prompt-caching strategy.

One breakpoint marks the system prompt and up to three mark recent non-tool
messages. Markers attach to content parts rather than message envelopes.
"""

from copy import deepcopy
from typing import Any

_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def _wrap_string_content_with_marker(msg: dict[str, Any]) -> None:
    """Convert ``content: str`` into a single text content-part and mark it."""
    text = msg.get("content") or ""
    msg["content"] = [
        {
            "type": "text",
            "text": text,
            "cache_control": dict(_EPHEMERAL),
        }
    ]


def _mark_last_text_part(parts: list[Any]) -> bool:
    """Mark the last text part, returning false when content has no safe anchor."""
    for part in reversed(parts):
        if isinstance(part, dict):
            part_type = part.get("type", "text")
            # Text-only marking avoids relying on less portable block semantics.
            if part_type == "text":
                part["cache_control"] = dict(_EPHEMERAL)
                return True
    return False


def _apply_marker(msg: dict[str, Any]) -> None:
    """Mark string or multipart content when a cacheable text anchor exists."""
    content = msg.get("content")
    if content is None or content == "":
        return
    if isinstance(content, str):
        _wrap_string_content_with_marker(msg)
        return
    if isinstance(content, list) and content:
        _mark_last_text_part(content)


def apply_anthropic_cache_markers(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deep-copy messages and add at most four prompt-cache breakpoints."""
    if not messages:
        return messages

    result = deepcopy(messages)

    used = 0
    first = result[0]
    if first.get("role") == "system":
        _apply_marker(first)
        used += 1

    body_indices: list[int] = []
    for idx in range(len(result)):
        msg = result[idx]
        role = msg.get("role", "")
        if role == "system":
            continue
        if role == "tool":
            # Tool results do not consume a rolling conversation breakpoint.
            continue
        body_indices.append(idx)

    # The system marker, when present, leaves three rolling slots.
    remaining_slots = max(0, 4 - used)
    for idx in body_indices[-remaining_slots:]:
        _apply_marker(result[idx])

    return result


def is_anthropic_endpoint(base_url: str | None, provider_name: str | None) -> bool:
    """Detect Anthropic routing from either endpoint host or provider name."""
    if base_url and "anthropic.com" in base_url.lower():
        return True
    if provider_name and provider_name.lower() == "anthropic":
        return True
    return False
