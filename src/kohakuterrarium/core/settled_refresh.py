"""Restore completed background results before truncating stable history."""

from typing import Any

from kohakuterrarium.core.agent_message_history import (
    reload_conversation_under_branch_view,
    snapshot_needs_settled_tool_replay,
)
from kohakuterrarium.core.branch_continuation import live_branch_path
from kohakuterrarium.core.branch_conversation import build_branch_conversation
from kohakuterrarium.core.history_candidate import (
    final_prompt_messages,
    require_history_candidate,
    without_retention,
)
from kohakuterrarium.errors import ConflictError


def prepare_mutation_prefix(
    agent, index: int, replacement_content: Any = None
) -> dict[str, Any] | None:
    """Prepare an admitted settled prefix and target index, or no refresh."""
    conversation = agent.controller.conversation
    target = conversation.get_messages()[index]
    if target.role != "user":
        return None
    prefix = conversation.to_messages(include_metadata=True)[:index]
    if agent.session_store is None or not any(
        message.get("metadata", {}).get("tool_result_phase") == "pending"
        for message in prefix
    ):
        return None
    events = agent.session_store.get_events(agent.config.name)
    view = live_branch_path(agent)
    if not snapshot_needs_settled_tool_replay(events, view, prefix):
        return None
    turn = target.metadata.get("turn_index")
    branch = target.metadata.get("branch_id")
    if not isinstance(turn, int) or not isinstance(branch, int):
        raise ConflictError("Cannot identify pending-history mutation target")
    prepared = reload_conversation_under_branch_view(
        agent, view, events=events, apply=False
    )
    matches = [
        i
        for i, message in enumerate(prepared["messages"])
        if message.get("role") == "user"
        and message.get("metadata", {}).get("turn_index") == turn
        and message.get("metadata", {}).get("branch_id") == branch
    ]
    if len(matches) != 1:
        raise ConflictError("Cannot restore pending-history mutation target")
    prepared = {**prepared, "messages": prepared["messages"][: matches[0] + 1]}
    relaxed = without_retention(conversation)
    candidate = build_branch_conversation(relaxed, prepared["messages"])
    candidate_messages = candidate.get_messages()
    candidate_index = next(
        (
            i
            for i, message in enumerate(candidate_messages)
            if message.role == "user"
            and message.metadata.get("turn_index") == turn
            and message.metadata.get("branch_id") == branch
        ),
        None,
    )
    if candidate_index is None:
        raise ConflictError("History retention would remove the mutation target")
    require_history_candidate(
        agent,
        final_prompt_messages(
            candidate_messages,
            candidate_index,
            (
                candidate_messages[candidate_index].content
                if replacement_content is None
                else replacement_content
            ),
        ),
    )
    return {
        "prepared": prepared,
        "conversation": candidate,
        "target_index": candidate_index,
    }
