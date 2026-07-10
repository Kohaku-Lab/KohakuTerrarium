"""Orphan tool-call / tool-result sanitation for :class:`Conversation`.

Split out of :mod:`conversation` to keep that module under the
file-size guard.
"""

from datetime import datetime
from typing import Any

from kohakuterrarium.llm.message import TextPart, messages_to_dicts
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _is_empty_content(content: Any) -> bool:
    """Return True if a message's ``content`` carries no user-visible text.

    Used by the orphan tool-call sanitiser to decide whether an assistant
    message whose ``tool_calls`` were all dropped can be removed wholesale.
    Treats ``None``, the empty string (after strip), and an empty list as
    empty. A list with any non-trivial part (text with content, image,
    file) counts as non-empty — the assistant still has something to say.
    """
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
                # Post-serialisation dicts — treat anything non-text or
                # non-empty text as meaningful payload.
                if part.get("type") == "text":
                    text = part.get("text", "")
                    if text and text.strip():
                        return False
                else:
                    return False
            else:
                # Any non-TextPart object (ImagePart, FilePart, …) is
                # meaningful — keep the message.
                return False
        return True
    return False


def sanitize_orphan_tool_pairs(
    messages: list[dict[str, Any]],
    *,
    preserve_pending_tail: bool = False,
) -> list[dict[str, Any]]:
    """Strip unmatched tool_call / tool-result pairs.

    Pure function: takes the provider payload, returns a new list
    with orphan fragments removed. Idempotent — running twice
    yields identical output.

    Rules (matches the OpenAI Chat Completions contract):

    1. Every id in an ``assistant.tool_calls`` list MUST have a
       matching ``role=tool`` message with the same ``tool_call_id``
       somewhere between that assistant message and the next
       ``assistant`` / ``user`` message. Unmatched ids are dropped
       from ``tool_calls``. If an assistant message ends up with
       empty ``tool_calls`` AND empty ``content``, the whole
       message is dropped.
    2. Every ``role=tool`` message MUST reference a ``tool_call_id``
       announced by some *preceding* assistant message (after the
       same sanitisation pass). Orphan tool messages are dropped.

    ``preserve_pending_tail`` protects the FINAL announcement when it
    is followed only by tool results (or nothing): its unmatched ids
    are in-flight calls whose results are still executing — a live
    mid-turn caller (the compact splice) must not delete them, or the
    arriving results become orphans.

    Produces WARNING-level log entries for every drop so operators
    can see when compaction left the conversation inconsistent.
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

    # --- Pass 1 + 2: scan for orphan assistant tool_calls. ---
    # For each assistant with tool_calls, walk forward until we hit
    # the next assistant/user and collect the tool_call_ids that
    # actually showed up. Drop the missing ones.
    cleaned: list[dict[str, Any]] = []
    n = len(messages)
    for idx, msg in enumerate(messages):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            expected_ids = [
                tc.get("id") for tc in msg["tool_calls"] if tc.get("id") is not None
            ]
            # Collect responder ids up to the next assistant/user.
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
                # All tool_calls orphaned — remove the key so the
                # provider doesn't see an empty list.
                new_msg.pop("tool_calls", None)

            # If the assistant now has NO meaningful payload, drop
            # the whole message. Content considered "empty" if it's
            # None, empty string, or empty list.
            if not kept_calls and _is_empty_content(new_msg.get("content")):
                logger.warning(
                    f"dropped assistant message #{idx} — no content + all tool_calls orphaned",
                    message_index=idx,
                )
                continue
            cleaned.append(new_msg)
        else:
            cleaned.append(msg)

    # --- Pass 3: drop orphan tool-result messages. ---
    # A tool message is valid only if some preceding assistant in
    # the (already sanitised) list advertises its tool_call_id.
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
    """Apply the orphan sanitiser to the in-memory list itself.

    ``to_messages`` sanitizes a fresh copy on every call, so orphans
    left in ``_messages`` re-warn on every LLM request. This prunes
    them once (the session store is untouched). Returns the number
    of messages removed.
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
