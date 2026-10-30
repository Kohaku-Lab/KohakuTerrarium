"""Restore persisted prefixes for canonical user-message mutations."""

from copy import deepcopy
from typing import Any, Protocol

from kohakuterrarium.core.agent.history.message_history import (
    replay_with_settled_tools,
    snapshot_needs_settled_tool_replay,
)
from kohakuterrarium.core.compact.branch import (
    compact_rule_from_event,
    is_summary_message,
)
from kohakuterrarium.core.conversation.conversation import Conversation
from kohakuterrarium.core.conversation.source import (
    PreparedHistorySource,
    copy_conversation,
)
from kohakuterrarium.core.conversation.snapshot import (
    prepare_snapshot,
    replayed_compact_state,
)
from kohakuterrarium.llm.message import dicts_to_messages
from kohakuterrarium.session.history import (
    compact_covered_event_ids,
    normalize_tool_call_events,
    replay_conversation,
)
from kohakuterrarium.session.raw_history import (
    RawHistoryPrefix,
    UserMessageSelector,
    select_raw_history_prefix,
)


class RawHistoryAgent(Protocol):
    session_store: Any
    config: Any
    controller: Any
    _turn_index: int
    _branch_id: int
    _parent_branch_path: list[tuple[int, int]]


def raw_target_content(
    agent: RawHistoryAgent,
    target: UserMessageSelector,
    *,
    branch_view: dict[int, int] | None = None,
):
    """Read canonical target content without changing agent state."""
    if agent.session_store is None:
        raise ValueError("raw persisted history is unavailable")
    prefix = select_raw_history_prefix(
        agent.session_store.get_events(agent.config.name),
        selector=target,
        branch_view=branch_view,
    )
    return prefix.target.get("content", "")


def _accepted_native_prefix(
    agent: RawHistoryAgent,
    prefix: RawHistoryPrefix,
    events: list[dict],
    source: PreparedHistorySource | None = None,
) -> Conversation | None:
    """Detach accepted context when every retained summary predates the target."""
    target = prefix.target
    turn, branch, event_id = (
        target["turn_index"],
        target["branch_id"],
        target["event_id"],
    )
    source = source or PreparedHistorySource.capture(agent)
    if source.cutoff >= event_id:
        return None
    if {t: b for t, b in source.view.items() if t <= turn} != {
        t: b for t, b in prefix.branch_view.items() if t <= turn
    }:
        return None
    if (
        sum(
            event.get("type") == "user_message"
            and event.get("turn_index") == turn
            and event.get("branch_id") == branch
            for event in events
        )
        != 1
    ):
        return None
    conversation = source.conversation
    messages = conversation.get_messages()
    matches = [
        i
        for i, message in enumerate(messages)
        if message.role == "user"
        and message.metadata.get("kind") != "tool_results"
        and message.metadata.get("turn_index") == turn
        and message.metadata.get("branch_id") == branch
    ]
    if len(matches) != 1:
        return None
    index = matches[0]
    summaries = [message for message in messages[:index] if is_summary_message(message)]
    if not summaries:
        return None
    for summary in summaries:
        completions = [
            event
            for event in events
            if event.get("type") == "compact_complete"
            and type(event.get("round")) is int
            and event["round"] > 0
            and isinstance(event.get("summary"), str)
            and summary.content
            == (
                f"[Previous context summary (compact round {event['round']})]"
                f"\n\n{event['summary']}"
            )
            and all(
                key in summary.metadata
                and key in event
                and summary.metadata[key] == event[key]
                for key in ("compact_source_path", "compact_result_event_ids")
            )
        ]
        if len(completions) != 1 or completions[0]["event_id"] >= event_id:
            return None
    detached = deepcopy(conversation)
    detached.config = conversation.config
    detached.truncate_from(index + 1)
    return detached


def prepare_raw_prefix_for_target(
    agent: RawHistoryAgent,
    target: UserMessageSelector,
    *,
    branch_view: dict[int, int] | None = None,
    source: PreparedHistorySource | None = None,
) -> PreparedHistorySource:
    """Prepare a detached canonical prefix without adopting history state."""
    if agent.session_store is None:
        raise ValueError("raw persisted history is unavailable")
    source = source or PreparedHistorySource.capture(agent)
    current_conversation = source.conversation
    all_events = list(agent.session_store.get_events(agent.config.name))
    prefix = select_raw_history_prefix(
        all_events,
        selector=target,
        branch_view=branch_view,
    )
    parent = tuple(
        (turn, branch)
        for turn, branch in prefix.branch_view.items()
        if 0 < turn < target.turn_index and branch > 0
    )
    native = _accepted_native_prefix(agent, prefix, all_events, source)
    if native is not None:
        return PreparedHistorySource(
            native,
            target.turn_index,
            target.branch_id,
            parent,
            source.cutoff,
            deepcopy(source.compact_state),
        )
    target_path: set[tuple[int, int]] = {
        (turn, branch)
        for turn, branch in prefix.branch_view.items()
        if turn < prefix.target.get("turn_index")
    }
    reusable_compacts = [
        evt
        for evt in all_events
        if (rule := compact_rule_from_event(evt)) is not None
        and (inputs := rule.inputs()) is not None
        and inputs.issubset(target_path)
    ]
    raw_events = [
        event
        for event in prefix.events
        if event.get("type")
        not in {
            "compact_replace",
            "compact_complete",
            "conversation_snapshot",
        }
    ]
    raw_events.extend(reusable_compacts)
    raw_events.append(prefix.target)
    # A covering compact can carry an event_id larger than the target's;
    # restore stream order so replay sees a monotonic event stream (its
    # pending-summary flush/drop logic assumes one).
    raw_events.sort(key=lambda evt: evt.get("event_id", 0))
    prepared = prepare_snapshot(
        agent.session_store,
        agent.config.name,
        all_events,
        prefix.branch_view,
        replay=replay_with_settled_tools,
        needs_replay=snapshot_needs_settled_tool_replay,
        before_turn=target.turn_index,
    )
    messages = (
        prepared["messages"]
        + replay_conversation([prefix.target], include_metadata=True)
        if prepared
        else replay_conversation(
            normalize_tool_call_events(
                raw_events,
                covered_event_ids=compact_covered_event_ids(
                    raw_events, prefix.branch_view
                ),
            ),
            branch_view=prefix.branch_view,
            include_metadata=True,
        )
    )
    persisted_messages = dicts_to_messages(messages)
    for raw_message, message in zip(messages, persisted_messages):
        metadata = raw_message.get("metadata")
        if isinstance(metadata, dict):
            message.metadata = deepcopy(metadata)
    if not any(message.role == "system" for message in persisted_messages):
        persisted_messages = [
            *(
                deepcopy(message)
                for message in current_conversation.get_messages()
                if message.role == "system"
            ),
            *persisted_messages,
        ]
    conversation = Conversation(current_conversation.config)
    for message in persisted_messages:
        conversation.append_message(message)
    return PreparedHistorySource(
        copy_conversation(conversation),
        target.turn_index,
        target.branch_id,
        parent,
        prepared.get("context_cutoff_event_id", 0) if prepared is not None else 0,
        deepcopy(
            prepared["compact_state"] if prepared else replayed_compact_state(messages)
        ),
    )


def reload_raw_prefix_for_target(
    agent: RawHistoryAgent,
    target: UserMessageSelector,
    *,
    branch_view: dict[int, int] | None = None,
) -> None:
    """Prepare and adopt the prefix for an existing canonical target."""
    prepare_raw_prefix_for_target(agent, target, branch_view=branch_view).adopt(
        agent, preserve_identity=False
    )
