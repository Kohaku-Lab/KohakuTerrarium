"""Sanitization of unmatched native tool calls and results."""

from datetime import datetime
from typing import Any

from kohakuterrarium.llm.message import TextPart, messages_to_dicts
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _is_empty_content(content: Any) -> bool:
    """Return whether content has no visible text or non-text payload."""
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        for part in content:
            if isinstance(part, TextPart):
                if part.text and part.text.strip():
                    return False
            elif isinstance(part, dict):
                # Serialized image and file parts remain meaningful without text.
                if part.get("type") == "text":
                    text = part.get("text", "")
                    if text and text.strip():
                        return False
                else:
                    return False
            else:
                # Any non-text content part keeps the assistant message meaningful.
                return False
        return True
    return False


def sanitize_orphan_tool_pairs(
    messages: list[dict[str, Any]],
    *,
    preserve_pending_tail: bool = False,
) -> list[dict[str, Any]]:
    """Return an idempotently sanitized provider message sequence.

    Each assistant call must receive a matching tool result before the next user
    or assistant message, and each tool result must reference a preceding retained
    call. ``preserve_pending_tail`` protects the final in-flight announcement so
    later results can still attach during mid-turn persistence or compaction.
    """
    if not messages:
        return messages

    protected_ids: set[str] = set()
    if preserve_pending_tail:
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.get("role") == "tool":
                continue
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                protected_ids = {
                    tc.get("id") for tc in msg["tool_calls"] if tc.get("id")
                }
            break

    # Tool results belong only to the assistant round before the next user or
    # assistant message; later matches cannot repair an earlier orphaned call.
    cleaned: list[dict[str, Any]] = []
    n = len(messages)
    for idx, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            expected_ids = [
                tc.get("id") for tc in msg["tool_calls"] if tc.get("id") is not None
            ]
            observed_ids: set[str] = set()
            for j in range(idx + 1, n):
                nxt = messages[j]
                if nxt.get("role") in ("assistant", "user"):
                    break
                if nxt.get("role") == "tool":
                    tc_id = nxt.get("tool_call_id")
                    if tc_id:
                        observed_ids.add(tc_id)

            kept_calls = [
                tc
                for tc in msg["tool_calls"]
                if tc.get("id") in observed_ids or tc.get("id") in protected_ids
            ]
            dropped = len(msg["tool_calls"]) - len(kept_calls)
            if dropped:
                missing = [
                    tc.get("id")
                    for tc in msg["tool_calls"]
                    if tc.get("id") not in observed_ids
                ]
                logger.warning(
                    f"dropped {dropped} orphan tool_call(s) on assistant message #{idx}",
                    dropped=dropped,
                    message_index=idx,
                    missing_ids=missing,
                    expected_ids=expected_ids,
                )
            new_msg = dict(msg)
            if kept_calls:
                new_msg["tool_calls"] = kept_calls
            else:
                # Providers reject an explicit empty native-call list.
                new_msg.pop("tool_calls", None)

            # An assistant shell with neither content nor calls is invalid context.
            if not kept_calls and _is_empty_content(new_msg.get("content")):
                logger.warning(
                    f"dropped assistant message #{idx} — no content + all tool_calls orphaned",
                    message_index=idx,
                )
                continue
            cleaned.append(new_msg)
        else:
            cleaned.append(msg)

    # Validate results against the already-sanitized assistant announcements.
    announced_ids: set[str] = set()
    final: list[dict[str, Any]] = []
    for idx, msg in enumerate(cleaned):
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tc_id = tc.get("id")
                if tc_id:
                    announced_ids.add(tc_id)
            final.append(msg)
        elif role == "tool":
            tc_id = msg.get("tool_call_id")
            if tc_id and tc_id in announced_ids:
                final.append(msg)
            else:
                logger.warning(
                    f"dropped orphan tool-result message #{idx} with id={tc_id}",
                    message_index=idx,
                    tool_call_id=tc_id,
                )
        else:
            final.append(msg)

    return final


def prune_orphan_tool_pairs(
    conversation: Any, *, preserve_pending_tail: bool = False
) -> int:
    """Prune in-memory orphan tool pairs and return the removal count.

    This prevents repeated warnings from copy-only wire sanitization without
    modifying the persisted session snapshot.
    """
    source = [dict(d) for d in messages_to_dicts(conversation._messages)]
    if len(source) != len(conversation._messages):
        return 0
    for idx, msg in enumerate(source):
        msg["_prune_index"] = idx
    kept = sanitize_orphan_tool_pairs(
        source, preserve_pending_tail=preserve_pending_tail
    )
    kept_by_index = {
        m["_prune_index"]: m for m in kept if isinstance(m.get("_prune_index"), int)
    }
    removed = len(conversation._messages) - len(kept_by_index)
    pruned: MessageList = []
    changed = removed > 0
    for idx, original in enumerate(conversation._messages):
        sanitized = kept_by_index.get(idx)
        if sanitized is None:
            continue
        kept_calls = sanitized.get("tool_calls")
        original_calls = (
            original.get("tool_calls")
            if isinstance(original, dict)
            else getattr(original, "tool_calls", None)
        )
        if original_calls and original_calls != kept_calls:
            changed = True
            if isinstance(original, dict):
                original = dict(original)
                if kept_calls:
                    original["tool_calls"] = kept_calls
                else:
                    original.pop("tool_calls", None)
            else:
                original.tool_calls = kept_calls or None
        pruned.append(original)
    if changed:
        conversation._messages = pruned
        conversation._metadata.message_count = len(pruned)
        conversation._metadata.updated_at = datetime.now()
    return removed
