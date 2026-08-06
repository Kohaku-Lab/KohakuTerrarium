"""Raw persisted-history helpers for message branch mutations."""

from typing import Any, Protocol

from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.llm.message import dicts_to_messages
from kohakuterrarium.session.history import replay_conversation
from kohakuterrarium.session.raw_history import (
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


def reload_raw_prefix_for_target(
    agent: RawHistoryAgent,
    target: UserMessageSelector,
    *,
    branch_view: dict[int, int] | None = None,
) -> None:
    """Reseat one agent on the branch prefix ending at target.

    The target branch's compaction baseline is preserved: a compact_replace
    that covers the target is kept but truncated so it never swallows the
    edited turn — earlier turns stay summarized, the target is materialized
    verbatim, and the new branch compacts independently from here on.
    """
    if agent.session_store is None:
        raise ValueError("raw persisted history is unavailable")
    current_conversation = agent.controller.conversation
    all_events = list(agent.session_store.get_events(agent.config.name))
    prefix = select_raw_history_prefix(
        all_events,
        selector=target,
        branch_view=branch_view,
    )
    # A compact_replace that fired AFTER the target in the event stream still
    # covers it by id range. Keep its summary as the branch's compact baseline
    # but cap the range just below the target so the edited turn materializes.
    target_id = prefix.target.get("event_id")
    covering_compacts: list[dict[str, Any]] = []
    if isinstance(target_id, int):
        for evt in all_events:
            if evt.get("type") not in ("compact_replace", "compact_complete"):
                continue
            frm = evt.get("replaced_from_event_id")
            to = evt.get("replaced_to_event_id")
            if not isinstance(frm, int) or not isinstance(to, int):
                continue
            # Keep the branch's compaction baseline whenever it covers
            # anything BEFORE the target; cap the range below the target so
            # the edited turn is never swallowed by the summary. A compact
            # that starts at/after the target has nothing before it to keep.
            if frm < target_id:
                capped = dict(evt)
                if to >= target_id:
                    capped["replaced_to_event_id"] = target_id - 1
                covering_compacts.append(capped)
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
    raw_events.extend(covering_compacts)
    raw_events.append(prefix.target)
    # A covering compact can carry an event_id larger than the target's;
    # restore stream order so replay sees a monotonic event stream (its
    # pending-summary flush/drop logic assumes one).
    raw_events.sort(key=lambda evt: evt.get("event_id", 0))
    messages = replay_conversation(
        raw_events,
        branch_view=prefix.branch_view,
        include_metadata=True,
    )
    persisted_messages = dicts_to_messages(messages)
    for raw_message, message in zip(messages, persisted_messages):
        metadata = raw_message.get("metadata")
        if isinstance(metadata, dict):
            message.metadata = dict(metadata)
    if not any(message.role == "system" for message in persisted_messages):
        persisted_messages = [
            *(
                message
                for message in agent.controller.conversation.get_messages()
                if message.role == "system"
            ),
            *persisted_messages,
        ]
    conversation = Conversation(current_conversation.config)
    for message in persisted_messages:
        conversation.append_message(message)
    agent.controller.conversation = conversation
    agent._turn_index = target.turn_index
    agent._branch_id = target.branch_id
    agent._parent_branch_path = [
        (turn, branch)
        for turn, branch in prefix.branch_view.items()
        if turn < target.turn_index
    ]
