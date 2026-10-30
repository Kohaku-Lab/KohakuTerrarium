"""Restore completed background results before truncating stable history."""

from kohakuterrarium.core.agent.history.message_history import (
    reload_conversation_under_branch_view,
    snapshot_needs_settled_tool_replay,
)
from kohakuterrarium.core.conversation.source import (
    PreparedHistorySource,
    prepare_branch_source,
)
from kohakuterrarium.errors import ConflictError


def prepare_mutation_prefix(
    agent, source: PreparedHistorySource, index: int
) -> tuple[PreparedHistorySource, int]:
    """Prepare saved results for a detached pending prefix and locate its target."""
    conversation = source.conversation
    target = conversation.get_messages()[index]
    if target.role != "user":
        return source, index
    prefix = conversation.to_messages(include_metadata=True)[:index]
    if agent.session_store is None or not any(
        message.get("metadata", {}).get("tool_result_phase") == "pending"
        for message in prefix
    ):
        return source, index
    events = agent.session_store.get_events(agent.config.name)
    view = source.view
    if not snapshot_needs_settled_tool_replay(events, view, prefix):
        return source, index
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
    result = prepare_branch_source(agent, source, prepared)
    candidate = result.conversation
    if not any(
        message.role == "user"
        and message.metadata.get("turn_index") == turn
        and message.metadata.get("branch_id") == branch
        for message in candidate.get_messages()
    ):
        raise ConflictError("History retention would remove the mutation target")
    return result, next(
        i
        for i, message in enumerate(candidate.get_messages())
        if message.role == "user"
        and message.metadata.get("turn_index") == turn
        and message.metadata.get("branch_id") == branch
    )


def refresh_mutation_prefix(agent, index: int) -> int:
    """Refresh a live pending prefix using a detached candidate."""
    source = PreparedHistorySource.capture(agent)
    result, index = prepare_mutation_prefix(agent, source, index)
    if result is not source:
        result.adopt(agent)
    return index
