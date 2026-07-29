"""Branch-aware history lookup helpers for agent message mutations."""

from collections.abc import Callable
from typing import Any

from kohakuterrarium.core.message_locator import (
    user_message_indices_for_content,
    user_message_indices_for_turn,
)
from kohakuterrarium.session.history import (
    replay_conversation,
    resolve_branch_view_strict,
    select_live_event_ids,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def resolve_edit_message_index(
    agent: Any,
    msgs: list[object],
    message_idx: int,
    *,
    turn_index: int | None = None,
    user_position: int | None = None,
    branch_view: dict[int, int] | None = None,
) -> int | None:
    """Resolve by turn metadata or exact unique legacy-content matching."""
    if turn_index is not None:
        metadata_matches = user_message_indices_for_turn(msgs, turn_index)
        if len(metadata_matches) == 1:
            return metadata_matches[0]
        if len(metadata_matches) > 1:
            logger.warning(
                "Ambiguous edit target metadata",
                turn_index=turn_index,
                matches=len(metadata_matches),
            )
            return None

        target_content = user_message_content_for_turn(
            agent,
            turn_index,
            branch_view=branch_view,
        )
        if target_content is not None:
            content_matches = user_message_indices_for_content(msgs, target_content)
            if len(content_matches) == 1:
                return content_matches[0]
            logger.warning(
                "Cannot uniquely match edit target content",
                turn_index=turn_index,
                matches=len(content_matches),
            )
            return None

        if user_position is None:
            return None
    if user_position is not None:
        if user_position < 0:
            return None
        seen = -1
        for idx, msg in enumerate(msgs):
            if msg.role != "user":
                continue
            seen += 1
            if seen == user_position:
                return idx
        return None
    if message_idx < 0 or message_idx >= len(msgs):
        return None
    return message_idx


def user_position_for_turn_index(
    agent: Any,
    turn_index: int,
    *,
    branch_view: dict[int, int] | None = None,
) -> int | None:
    """Return the visible user-position for a live turn_index."""
    for pos, candidate in enumerate(live_user_turns(agent, branch_view=branch_view)):
        if candidate == turn_index:
            return pos
    return None


def live_user_turns(
    agent: Any,
    *,
    branch_view: dict[int, int] | None = None,
) -> list[int]:
    """Return live user turn indices in visible order."""
    if agent.session_store is None:
        return []
    try:
        events = agent.session_store.get_events(agent.config.name)
    except Exception as exc:
        logger.warning(
            "Failed to read events for live turns",
            error=str(exc),
            exc_info=True,
        )
        return []
    live_ids = select_live_event_ids(events, branch_view=branch_view)
    seen_turns: set[int] = set()
    turns: list[tuple[int, int]] = []
    for event in events:
        if event.get("type") != "user_message":
            continue
        event_id = event.get("event_id")
        turn_index = event.get("turn_index")
        if not isinstance(event_id, int) or not isinstance(turn_index, int):
            continue
        if event_id not in live_ids or turn_index in seen_turns:
            continue
        seen_turns.add(turn_index)
        turns.append((turn_index, event_id))
    turns.sort(key=lambda pair: pair[0])
    return [turn_index for turn_index, _ in turns]


def turn_index_for_user_position(
    agent: Any,
    user_position: int,
    *,
    branch_view: dict[int, int] | None = None,
) -> int | None:
    """Return the live turn at one visible user-message position."""
    turns = live_user_turns(agent, branch_view=branch_view)
    if user_position < 0 or user_position >= len(turns):
        return None
    return turns[user_position]


def max_branch_id_for_turn(agent: Any, turn_index: int) -> int:
    """Return the largest persisted branch id for one turn."""
    if agent.session_store is None:
        return 0
    try:
        events = agent.session_store.get_events(agent.config.name)
    except Exception as exc:
        logger.warning(
            "Failed to read events for branch lookup",
            error=str(exc),
            exc_info=True,
        )
        return 0
    max_branch = 0
    for event in events:
        if event.get("turn_index") != turn_index:
            continue
        branch_id = event.get("branch_id")
        if isinstance(branch_id, int) and branch_id > max_branch:
            max_branch = branch_id
    return max_branch


def user_message_content_for_turn(
    agent: Any,
    turn_index: int,
    *,
    branch_view: dict[int, int] | None = None,
):
    """Return persisted user content from the selected branch of one turn."""
    if agent.session_store is None:
        return None
    try:
        events = agent.session_store.get_events(agent.config.name)
    except Exception as exc:
        logger.warning(
            "Failed to read events for turn-content lookup",
            error=str(exc),
            exc_info=True,
        )
        return None
    selected = resolve_branch_view_strict(events, branch_view)
    target_branch = selected.get(turn_index)
    if target_branch is None:
        return None
    for event in events:
        if (
            event.get("type") == "user_message"
            and event.get("turn_index") == turn_index
            and event.get("branch_id") == target_branch
        ):
            return event.get("content")
    return None


def reload_conversation_under_branch_view(
    agent: Any,
    branch_view: dict[int, int],
    *,
    replay: Callable[..., list[dict[str, Any]]] = replay_conversation,
) -> None:
    """Replay a selected subtree and align conversation and branch state."""
    if agent.session_store is None:
        return
    try:
        events = agent.session_store.get_events(agent.config.name)
    except Exception as exc:
        logger.warning(
            "Failed to read events for branch_view reload",
            error=str(exc),
            exc_info=True,
        )
        return

    selected = resolve_branch_view_strict(events, branch_view)
    messages = replay(events, branch_view=branch_view)
    metadata_by_turn = {
        int(event["turn_index"]): {
            "event_id": event.get("event_id"),
            "turn_index": event.get("turn_index"),
            "branch_id": event.get("branch_id"),
        }
        for event in events
        if event.get("type") == "user_message"
        and isinstance(event.get("turn_index"), int)
    }
    for message in messages:
        if message.get("role") != "user":
            continue
        for metadata in metadata_by_turn.values():
            if message.get("content") == next(
                (
                    event.get("content")
                    for event in events
                    if event.get("event_id") == metadata.get("event_id")
                ),
                None,
            ):
                message["metadata"] = metadata
                break

    conversation = agent.controller.conversation
    existing_system = [
        message for message in conversation.get_messages() if message.role == "system"
    ]
    conversation._messages.clear()
    conversation._messages.extend(existing_system)
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        extra: dict = {}
        for key in ("tool_calls", "tool_call_id", "name", "metadata"):
            if message.get(key):
                extra[key] = message[key]
        conversation.append(role, message.get("content", ""), **extra)

    if selected:
        max_turn = max(selected)
        agent._turn_index = max_turn
        agent._branch_id = selected[max_turn]
        agent._parent_branch_path = [
            (turn, branch)
            for turn, branch in sorted(selected.items())
            if turn < max_turn
        ]
    else:
        agent._turn_index = 0
        agent._branch_id = 0
        agent._parent_branch_path = []


def previous_branch_user_content(agent: Any):
    """Return user content from the nearest lower branch of the current turn."""
    if agent.session_store is None:
        return None
    try:
        events = agent.session_store.get_events(agent.config.name)
    except Exception as exc:
        logger.warning(
            "Failed to read events for prev-branch user",
            error=str(exc),
            exc_info=True,
        )
        return None
    latest_for_turn: dict | None = None
    latest_branch = -1
    for event in events:
        if (
            event.get("type") != "user_message"
            or event.get("turn_index") != agent._turn_index
        ):
            continue
        branch_id = event.get("branch_id")
        if not isinstance(branch_id, int):
            continue
        if branch_id < agent._branch_id and branch_id > latest_branch:
            latest_branch = branch_id
            latest_for_turn = event
    return latest_for_turn.get("content") if latest_for_turn is not None else None
