"""Conversation splice for compaction.

Split out of :mod:`compact` to keep that module under the file-size
guard.
"""

from datetime import datetime
from typing import Any

from kohakuterrarium.llm.message import create_message


def prefix_fingerprint(messages: list) -> tuple:
    """Content fingerprint of the to-be-summarized prefix.

    Object identity alone misses IN-PLACE edits: an edit flow that
    rewrites ``msg.content`` on the same object would pass the
    ``expected_last is`` check and be silently discarded under a
    summary generated from the OLD content.
    """
    out = []
    for m in messages:
        to_dict = getattr(m, "to_dict", None)
        if callable(to_dict):
            # Canonical serializer — covers tool_call_id / name /
            # extra provider fields, not just role+content+tool_calls.
            out.append(repr(to_dict()))
        else:
            out.append(
                (
                    getattr(m, "role", None),
                    repr(getattr(m, "content", None)),
                    repr(getattr(m, "tool_calls", None)),
                )
            )
    return tuple(out)


def splice_conversation(
    conversation: Any,
    boundary: int,
    summary: str,
    compact_round: int,
    expected_last: Any = None,
    expected_fingerprint: tuple | None = None,
) -> bool:
    """Atomic splice: replace compact zone with summary message.

    Returns ``False`` (conversation untouched) when the list no
    longer matches what was summarized — the summary LLM call runs
    for a while and a rewind / edit / emergency drop may land in
    the gap.
    """
    messages = conversation.get_messages()

    if boundary >= len(messages) or boundary <= 1:
        return False
    if expected_last is not None and messages[boundary - 1] is not expected_last:
        return False
    if (
        expected_fingerprint is not None
        and prefix_fingerprint(messages[1:boundary]) != expected_fingerprint
    ):
        return False
    # The boundary must already be tool-safe (computed BEFORE the
    # summarizer ran — moving it here would keep already-summarized
    # content raw, duplicating it). A tool-result head means the
    # conversation changed shape: reject.
    if getattr(messages[boundary], "role", None) == "tool":
        return False

    system_msg = messages[0]  # The system prompt survives every compaction round.
    live_zone = messages[boundary:]  # Messages after the boundary remain verbatim.

    conversation._messages.clear()
    conversation._messages.append(system_msg)

    # Add summary as an assistant message with a marker
    summary_msg = create_message(
        "assistant",
        f"[Previous context summary (compact round {compact_round})]\n\n{summary}",
    )
    conversation._messages.append(summary_msg)

    # Restore live zone
    conversation._messages.extend(live_zone)

    # Contain any residual mis-cut once, in memory only. The splice
    # runs MID-TURN: the live tail's final announcement may have
    # results still executing — preserve it or the arriving results
    # become orphans.
    if hasattr(conversation, "prune_orphan_tool_pairs"):
        conversation.prune_orphan_tool_pairs(preserve_pending_tail=True)

    # Update metadata
    conversation._metadata.message_count = len(conversation._messages)
    conversation._metadata.total_chars = conversation.get_context_length()
    conversation._metadata.updated_at = datetime.now()
    return True


def count_keep_messages(messages: list, keep_recent_turns: int) -> int:
    """Count how many messages from the end to keep (live zone).

    Two-phase policy:

    1. **Walk back ``keep_recent_turns`` user turns.** This is the
       normal case — preserve the recent turns + assistant/tool
       messages between them.

    2. **Half-cap fallback** when phase 1 cannot find enough user
       turns. Without this an agent run with 100 tool calls but
       only 2 user turns would always report ``boundary = 1``
       ("too_short") even though there is plenty to summarise. The
       fallback only applies once the conversation has at least
       ``MIN_COMPACTABLE`` messages so a tiny chat (a couple
       messages) is still considered too small to bother with.
    """
    # Below this many messages the compact zone is so small there
    # is nothing useful to summarise — skip and report ``too_short``.
    MIN_COMPACTABLE = 8

    n = len(messages)
    if n <= 1:
        return 0
    turns = 0
    by_turn_count = 0
    found_target = False
    for msg in reversed(messages):
        by_turn_count += 1
        if msg.role == "user":
            turns += 1
            if turns >= keep_recent_turns:
                found_target = True
                break

    if found_target:
        # Normal path — keep the requested user-turn window plus
        # whatever tool / assistant messages sit inside it.
        return min(by_turn_count, n - 1)

    # Fallback only kicks in when the conversation is long enough
    # that the half-cap leaves a non-trivial compact zone.
    if n < MIN_COMPACTABLE:
        return min(by_turn_count, n - 1)

    half_cap = max(1, n // 2)
    return min(half_cap, n - 1)
