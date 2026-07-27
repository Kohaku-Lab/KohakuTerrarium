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
    """Reseat one agent on the uncompacted prefix ending at target."""
    if agent.session_store is None:
        raise ValueError("raw persisted history is unavailable")
    prefix = select_raw_history_prefix(
        agent.session_store.get_events(agent.config.name),
        selector=target,
        branch_view=branch_view,
    )
    raw_events = [
        event
        for event in [*prefix.events, prefix.target]
        if event.get("type") not in {"compact_replace", "conversation_snapshot"}
    ]
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
    conversation = Conversation()
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
