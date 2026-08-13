"""Conversation splice for compaction.

Split out of :mod:`compact` to keep that module under the file-size
guard.
"""

from datetime import datetime
from typing import Any

from kohakuterrarium.core.conversation_elide import (
    estimate_message_tokens,
    estimate_messages_tokens,
)
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
    retain_latest_user: bool = False,
) -> bool:
    """Atomic splice: replace compact zone with summary message.

    Returns ``False`` (conversation untouched) when the list no
    longer matches what was summarized — the summary LLM call runs
    for a while and a rewind / edit / emergency drop may land in
    the gap.
    """
    messages = conversation.get_messages()

    if boundary > len(messages) or boundary <= 1:
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
    if boundary < len(messages) and getattr(messages[boundary], "role", None) == "tool":
        return False

    system_msg = messages[0]  # The system prompt survives every compaction round.
    live_zone = messages[boundary:]  # Messages after the boundary remain verbatim.
    retained_user = None
    if retain_latest_user:
        retained_user = next(
            (
                msg
                for msg in reversed(messages[1:boundary])
                if is_real_user_message(msg)
            ),
            None,
        )

    conversation._messages.clear()
    conversation._messages.append(system_msg)

    # Add summary as an assistant message with a marker
    summary_msg = create_message(
        "assistant",
        f"[Previous context summary (compact round {compact_round})]\n\n{summary}",
    )
    conversation._messages.append(summary_msg)
    if retained_user is not None:
        conversation._messages.append(retained_user)

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


def is_real_user_message(msg: Any) -> bool:
    """Return whether a message represents explicit user input."""
    if getattr(msg, "role", None) != "user":
        return False
    metadata = getattr(msg, "metadata", None)
    return not isinstance(metadata, dict) or metadata.get("kind") != "tool_results"


def select_compact_boundary(
    messages: list,
    keep_recent_turns: int,
    *,
    target_tokens: int | None = None,
    prompt_tokens: int = 0,
    summary_tokens: int = 0,
) -> int:
    """Select the old turn boundary, tightening it only when over budget."""
    n = len(messages)
    boundary = n - count_keep_messages(messages, keep_recent_turns)
    while boundary > 1 and boundary < n and messages[boundary].role == "tool":
        boundary -= 1

    if target_tokens is None or prompt_tokens <= 0:
        return boundary

    estimated_history = estimate_messages_tokens(messages)
    fixed_tokens = max(0, prompt_tokens - estimated_history)
    if fixed_tokens + summary_tokens >= target_tokens:
        return boundary
    live_budget = max(1, target_tokens - fixed_tokens - summary_tokens)
    live_tokens = estimate_message_tokens(messages[0]) + sum(
        estimate_message_tokens(msg) for msg in messages[boundary:]
    )
    if live_tokens <= live_budget:
        return boundary

    suffix_tokens = estimate_message_tokens(messages[0])
    token_boundary = n
    for index in range(n - 1, 0, -1):
        message_tokens = estimate_message_tokens(messages[index])
        if suffix_tokens + message_tokens > live_budget:
            break
        suffix_tokens += message_tokens
        token_boundary = index

    while token_boundary < n and messages[token_boundary].role == "tool":
        token_boundary += 1

    pending_start = _pending_tool_round_start(messages)
    if pending_start is not None:
        token_boundary = min(token_boundary, pending_start)
    return max(boundary, token_boundary)


def _pending_tool_round_start(messages: list) -> int | None:
    result_ids: set[str] = set()
    for index in range(len(messages) - 1, 0, -1):
        msg = messages[index]
        if getattr(msg, "role", None) == "tool":
            tool_call_id = getattr(msg, "tool_call_id", None)
            if tool_call_id:
                result_ids.add(tool_call_id)
            continue
        calls = getattr(msg, "tool_calls", None)
        if getattr(msg, "role", None) == "assistant" and calls:
            call_ids = {call.get("id") for call in calls if call.get("id")}
            if len(call_ids) != len(calls) or not call_ids.issubset(result_ids):
                return index
        return None
    return None


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
        if is_real_user_message(msg):
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
