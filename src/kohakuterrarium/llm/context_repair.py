"""Pure conversation edits that remove content a provider refused.

A ``ContextRepair`` is applied twice: to the in-flight request so the retry can
succeed, and to the host conversation so later turns do not resend the refused
content. Both sides run the same edit over provider-shaped message dicts.
"""

import re
from dataclasses import dataclass
from typing import Any, Literal

from kohakuterrarium.llm.recovery import drop_last_tool_round

MEDIA_PART_TYPES = frozenset({"image_url", "input_image", "image"})
REASON_LIMIT = 300
_HTML_TAG = re.compile(r"</?[A-Za-z][^<>]*>")

RepairKind = Literal["strip_media", "drop_tool_round"]
RepairScope = Literal["newest", "all"]


def _short(reason: str) -> str:
    # Proxies answer size refusals with HTML pages; the model needs only their text.
    text = " ".join(_HTML_TAG.sub(" ", str(reason or "")).split())
    return text if len(text) <= REASON_LIMIT else text[: REASON_LIMIT - 1] + "…"


def media_note(count: int, reason: str) -> str:
    """Text that replaces removed media, telling the model what happened."""
    why = _short(reason) or "the provider rejected the request"
    return (
        f"[{count} image(s) removed: the model provider rejected the request "
        f"({why}). The image(s) were not seen. If they are still needed, read "
        "them again smaller — fewer PDF pages per read, or a smaller image.]"
    )


def drop_note(reason: str) -> str:
    """Text appended to an emergency tool-round drop caused by a rejection."""
    why = _short(reason) or "the provider rejected the request"
    return f"\n\nThe model provider rejected the request: {why}"


def _is_media(part: Any) -> bool:
    return isinstance(part, dict) and part.get("type") in MEDIA_PART_TYPES


def _media_count(message: dict[str, Any]) -> int:
    content = message.get("content")
    if message.get("role") not in {"user", "tool"} or not isinstance(content, list):
        return 0
    return sum(_is_media(part) for part in content)


def _strip_message(message: dict[str, Any], reason: str) -> dict[str, Any]:
    parts: list[Any] = []
    removed = 0
    for part in message["content"]:
        if _is_media(part):
            removed += 1
            continue
        parts.append(part)
    parts.append({"type": "text", "text": media_note(removed, reason)})
    return {**message, "content": parts}


def strip_media(
    messages: list[dict[str, Any]], scope: RepairScope, reason: str = ""
) -> tuple[int, list[dict[str, Any]]]:
    """Replace image parts with a note; ``newest`` edits one message, ``all`` every one."""
    counts = [_media_count(message) for message in messages]
    targets = [i for i, count in enumerate(counts) if count]
    if not targets:
        return 0, messages
    if scope == "newest":
        targets = targets[-1:]
    result = list(messages)
    for index in targets:
        result[index] = _strip_message(messages[index], reason)
    return sum(counts[i] for i in targets), result


def drop_tool_round(
    messages: list[dict[str, Any]], reason: str = ""
) -> tuple[int, list[dict[str, Any]]]:
    """Splice out the newest tool round, naming the rejection in its placeholder."""
    dropped, recovered = drop_last_tool_round(messages)
    if not dropped or not reason:
        return dropped, recovered
    for index in range(len(recovered) - 1, -1, -1):
        message = recovered[index]
        content = message.get("content")
        if message.get("role") == "user" and str(content).startswith(
            "[tool-result truncated]"
        ):
            recovered[index] = {**message, "content": content + drop_note(reason)}
            break
    return dropped, recovered


@dataclass(frozen=True)
class ContextRepair:
    """One conversation edit, replayable on any provider-shaped message list."""

    kind: RepairKind
    scope: RepairScope = "newest"
    reason: str = ""

    def apply(self, messages: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
        """Return ``(items_changed, edited_messages)``; the input is not mutated."""
        if self.kind == "strip_media":
            return strip_media(messages, self.scope, self.reason)
        return drop_tool_round(messages, self.reason)


CONTENT_REPAIR_LADDER = (
    ("strip_media", "newest"),
    ("strip_media", "all"),
    ("drop_tool_round", "newest"),
)
