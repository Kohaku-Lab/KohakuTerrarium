"""Locate canonical user messages without relying on conversation positions."""

import json

from kohakuterrarium.llm.message import TextPart, normalize_content_parts


def _content_signature(content: object) -> tuple[str, str]:
    """Return a stable text/attachment signature for persisted message content."""
    normalized = normalize_content_parts(content)  # type: ignore[arg-type]
    if isinstance(normalized, str):
        return normalized, "[]"
    if not isinstance(normalized, list):
        return "", "[]"

    text = "\n\n".join(part.text for part in normalized if isinstance(part, TextPart))
    attachments = [
        part.to_dict() for part in normalized if not isinstance(part, TextPart)
    ]
    return text, json.dumps(
        attachments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def user_message_indices_for_turn(
    messages: list[object],
    turn_index: int,
) -> list[int]:
    """Return user-message indices carrying the requested persisted turn."""
    indices: list[int] = []
    for idx, msg in enumerate(messages):
        metadata = getattr(msg, "metadata", None)
        if (
            getattr(msg, "role", None) == "user"
            and isinstance(metadata, dict)
            and metadata.get("turn_index") == turn_index
        ):
            indices.append(idx)
    return indices


def user_message_indices_for_content(
    messages: list[object],
    content: object,
) -> list[int]:
    """Return user-message indices whose normalized content matches exactly."""
    signature = _content_signature(content)
    return [
        idx
        for idx, msg in enumerate(messages)
        if getattr(msg, "role", None) == "user"
        and _content_signature(getattr(msg, "content", None)) == signature
    ]
