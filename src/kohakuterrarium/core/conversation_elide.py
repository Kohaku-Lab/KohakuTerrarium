"""Elide stale tool-result content from a conversation.

Tool results are kept verbatim only for the LATEST feedback round —
the trailing run of tool-feedback messages. Every earlier round is
reduced in place to a short head plus a recovery notice, so the
controller still sees WHAT it called without paying the full context
cost every turn. Full outputs stay in the session event log, so the
agent can re-run the tool or use ``search_memory`` to get them back.
"""

from typing import Any

from kohakuterrarium.core.compact_text import extract_message_text
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Marker embedded in every stub; also the idempotence signal for
# messages whose metadata was lost (e.g. a resume round-trip).
ELISION_MARKER = "[tool output elided"

# ``metadata["kind"]`` value the controller stamps on bracket-mode
# tool-feedback user messages at append time.
TOOL_FEEDBACK_KIND = "tool_results"

HEAD_CHARS = 200
# Below this size a stub saves nothing — leave the message alone.
MIN_ELIDABLE_CHARS = 320


def _is_tool_feedback(msg: Any) -> bool:
    if getattr(msg, "role", None) == "tool":
        return True
    meta = getattr(msg, "metadata", None)
    return isinstance(meta, dict) and meta.get("kind") == TOOL_FEEDBACK_KIND


def _stub(text: str) -> str:
    dropped = len(text) - HEAD_CHARS
    return (
        f"{text[:HEAD_CHARS]}\n… {ELISION_MARKER} — {dropped} chars dropped. "
        "Re-run the tool or use search_memory if the full output is needed again]"
    )


def elide_stale_tool_results(conversation: Any) -> int:
    """Stub out every tool-feedback message before the latest round.

    The latest round is the trailing run of tool-feedback messages; when
    the conversation ends with anything else (assistant reply, fresh user
    input), every tool result is stale. Content is rewritten in place —
    role, ``tool_call_id`` and message order are preserved, so provider
    pairing and index-based edit/regen flows are unaffected. Returns the
    number of messages elided.
    """
    messages = conversation.get_messages()
    tail = len(messages)
    while tail > 0 and _is_tool_feedback(messages[tail - 1]):
        tail -= 1

    elided = 0
    for msg in messages[:tail]:
        if not _is_tool_feedback(msg):
            continue
        meta = getattr(msg, "metadata", None)
        if isinstance(meta, dict) and meta.get("tool_results_elided"):
            continue
        text = extract_message_text(msg) or ""
        if len(text) < MIN_ELIDABLE_CHARS or ELISION_MARKER in text[: HEAD_CHARS + 80]:
            continue
        msg.content = _stub(text)
        if isinstance(meta, dict):
            meta["tool_results_elided"] = True
        elided += 1

    if elided:
        conversation._metadata.total_chars = conversation.get_context_length()
        logger.debug("Stale tool results elided", count=elided)
    return elided
