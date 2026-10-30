"""Branch-aware history lookup helpers for agent message mutations."""

import re
from collections.abc import Callable
from typing import Any

from kohakuterrarium.core.branch_conversation import build_branch_conversation
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.conversation_elide import (
    ELISION_MARKER,
    TOOL_FEEDBACK_KIND,
    elide_stale_tool_results,
    estimate_tokens,
)
from kohakuterrarium.core.message_locator import (
    user_message_indices_for_content,
    user_message_indices_for_turn,
)
from kohakuterrarium.core.settled_identity import pending_snapshot_jobs
from kohakuterrarium.core.snapshot_history import (
    prepare_snapshot,
    replayed_compact_state,
)
from kohakuterrarium.core.tool_output import render_content_text
from kohakuterrarium.session.history import (
    compact_covered_event_ids,
    index_parent_paths,
    normalize_resumable_events,
    normalize_tool_call_events,
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
    maximum = getattr(agent.session_store, "max_branch_id", None)
    if callable(maximum):
        try:
            return maximum(agent.config.name, turn_index)
        except Exception as exc:
            logger.warning(
                "Branch index unavailable; reading raw events",
                error=str(exc),
                exc_info=True,
            )
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


def _tool_identity(event: dict[str, Any]) -> str:
    return str(event.get("call_id") or event.get("job_id") or "")


def include_settled_tool_results(
    events: list[dict[str, Any]],
    live_event_ids: set[int],
    *,
    covered_event_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Attach completed results to retained calls without rewriting settled pairs."""
    live_events = [
        event
        for event in events
        if not isinstance(event.get("event_id"), int)
        or event["event_id"] in live_event_ids
    ]
    live_call_turns: dict[str, Any] = {}
    for event in live_events:
        if event.get("type") == "tool_call" and (identity := _tool_identity(event)):
            if identity not in live_call_turns:
                live_call_turns[identity] = event.get("turn_index")
    live_result_ids: set[str] = {
        identity
        for event in live_events
        if event.get("type") == "tool_result" and (identity := _tool_identity(event))
    }
    late_results: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("type") != "tool_result":
            continue
        identity = _tool_identity(event)
        eid = event.get("event_id")
        if eid in (covered_event_ids or ()):
            continue
        if (
            identity
            and identity in live_call_turns
            and identity not in live_result_ids
            and identity not in late_results
        ):
            projected = dict(event)
            projected["_source_event_id"] = projected.pop("event_id", None)
            projected["_settled_from_call"] = True
            late_results[identity] = projected
        elif (
            identity
            and identity in live_call_turns
            and isinstance(eid, int)
            and eid in live_event_ids
            and identity not in late_results
        ):
            # Live results recorded in later turns are paired at their call.
            call_turn = live_call_turns[identity]
            result_turn = event.get("turn_index")
            if (
                isinstance(call_turn, int)
                and isinstance(result_turn, int)
                and result_turn > call_turn
            ):
                projected = dict(event)
                projected["_source_event_id"] = projected.pop("event_id", None)
                projected["_settled_from_call"] = True
                late_results[identity] = projected
    replay_events: list[dict[str, Any]] = []
    for event in live_events:
        identity = _tool_identity(event)
        if event.get("type") == "tool_result" and identity in late_results:
            continue
        replay_events.append(event)
        if event.get("type") == "tool_call" and identity in late_results:
            replay_events.append(late_results[identity])
    return replay_events


def replay_with_settled_tools(
    events: list[dict[str, Any]],
    *,
    branch_view: dict[int, int] | None = None,
    include_metadata: bool = False,
    resumable: bool = False,
) -> list[dict[str, Any]]:
    """Replay a branch, keeping completed results with their live calls."""
    parent_paths = index_parent_paths(events)
    live_event_ids = select_live_event_ids(
        events, branch_view=branch_view, parent_paths=parent_paths
    )
    covered_ids = compact_covered_event_ids(
        events, branch_view, parent_paths=parent_paths
    )
    replay_events = include_settled_tool_results(
        events, live_event_ids, covered_event_ids=covered_ids
    )
    if resumable:
        replay_events = normalize_resumable_events(
            replay_events, covered_event_ids=covered_ids
        )
    else:
        replay_events = normalize_tool_call_events(
            replay_events, covered_event_ids=covered_ids
        )
    return replay_conversation(
        replay_events,
        branch_view=branch_view,
        include_metadata=include_metadata,
    )


def snapshot_needs_settled_tool_replay(
    events: list[dict],
    branch_view: dict[int, int] | None,
    snapshot: list[dict] | None = None,
) -> bool:
    """Whether the snapshot still lacks a completed result for a live call."""
    if not events:
        return False
    parent_paths = index_parent_paths(events)
    live_ids = select_live_event_ids(
        events, branch_view=branch_view, parent_paths=parent_paths
    )
    covered_ids = compact_covered_event_ids(
        events, branch_view, parent_paths=parent_paths
    )
    projected = include_settled_tool_results(
        events, live_ids, covered_event_ids=covered_ids
    )
    needed: dict[str, set[str]] = {}
    provider_ids: dict[str, str] = {}
    alias_owners: dict[str, set[str]] = {}
    for event in projected:
        if event.get("type") != "tool_call":
            continue
        job_id = str(event.get("call_id") or event.get("job_id") or "")
        provider_id = str(event.get("tool_call_id") or "")
        if job_id:
            alias_owners.setdefault(job_id, set()).add(job_id)
            if provider_id:
                provider_ids[job_id] = provider_id
                alias_owners.setdefault(provider_id, set()).add(job_id)
    pending_jobs = pending_snapshot_jobs(snapshot or [], alias_owners)
    for event in projected:
        if event.get("type") != "tool_result" or event.get("event_id") in covered_ids:
            continue
        job_id = str(event.get("call_id") or event.get("job_id") or "")
        if not job_id or job_id not in alias_owners:
            continue
        names = {
            job_id,
            provider_ids.get(job_id, ""),
            str(event.get("tool_call_id") or ""),
        }
        names.discard("")
        if not event.get("_settled_from_call") and job_id not in pending_jobs:
            continue
        needed[job_id] = names
        for name in names:
            alias_owners.setdefault(name, set()).add(job_id)
    if not needed:
        return False
    if not snapshot:
        return True
    present: set[str] = set()
    for message in snapshot:
        identities = _snapshot_result_identities(message)
        if not identities:
            continue
        if not _snapshot_result_is_final(message):
            continue
        for identity in identities:
            owners = alias_owners.get(identity, set())
            if len(owners) == 1:
                present.update(owners & needed.keys())
    return any(job_id not in present for job_id in needed)


def _snapshot_result_identities(message: dict) -> set[str]:
    role = message.get("role")
    metadata = (
        message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    )
    names: set[str] = set()
    if role != "tool" and not (
        role == "user" and metadata.get("kind") == TOOL_FEEDBACK_KIND
    ):
        return names
    for key in ("job_id", "call_id"):
        if metadata.get(key):
            names.add(str(metadata[key]))
    job_ids = metadata.get("job_ids")
    if isinstance(job_ids, list):
        names.update(str(value) for value in job_ids if value)
    if names:
        return names
    if role == "tool":
        identity = str(message.get("tool_call_id") or "")
        return {identity} if identity else set()
    content = render_content_text(message.get("content") or "")
    names.update(
        re.findall(
            r"(?:\A|\n\n)\[Tool ([^\]\n]+?) "
            r"(?:completed|failed(?:, exit [^\]\n]+)?|cancelled)\](?=\n|\Z)",
            content,
        )
    )
    return names


def _snapshot_result_is_final(message: dict) -> bool:
    metadata = (
        message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    )
    phase = metadata.get("tool_result_phase")
    if phase == "pending":
        return False
    if phase == "final":
        return True
    content = render_content_text(message.get("content") or "")
    if re.match(
        r"^(?:\[[^\]\n]+\] )?(?:Running in background — task delegated\.|"
        r"Promoted to background\. Result arrives in a later turn\.)",
        content,
    ):
        return False
    if metadata.get("tool_results_elided") or ELISION_MARKER in content:
        return True
    if message.get("role") == "user" and metadata.get("kind") == TOOL_FEEDBACK_KIND:
        return bool(_snapshot_result_identities(message))
    return True


def reload_conversation_under_branch_view(
    agent: Any,
    branch_view: dict[int, int],
    *,
    replay: Callable[..., list[dict[str, Any]]] = replay_conversation,
    events: list[dict[str, Any]] | None = None,
    apply: bool = True,
):
    """Replay a selected subtree and optionally install it on the agent."""
    if agent.session_store is None:
        if apply:
            return None
        raise RuntimeError("Cannot prepare a selected branch without a session store")
    if events is None:
        events = agent.session_store.get_events(agent.config.name)

    selected = resolve_branch_view_strict(events, branch_view)
    prepared = prepare_snapshot(
        agent.session_store,
        agent.config.name,
        events,
        selected,
        replay=replay_with_settled_tools,
        needs_replay=snapshot_needs_settled_tool_replay,
    )
    if prepared is not None:
        if apply:
            apply_prepared_branch_view(agent, prepared)
            return None
        return prepared
    if replay is replay_conversation:
        messages = replay_with_settled_tools(
            events, branch_view=branch_view, include_metadata=True
        )
    else:
        live_event_ids = select_live_event_ids(events, branch_view=branch_view)
        covered_ids = compact_covered_event_ids(events, branch_view)
        replay_events = include_settled_tool_results(
            events, live_event_ids, covered_event_ids=covered_ids
        )
        messages = replay(
            normalize_tool_call_events(replay_events, covered_event_ids=covered_ids),
            branch_view=branch_view,
            include_metadata=True,
        )
    prepared = {"messages": messages, "selected": selected}
    if apply:
        apply_prepared_branch_view(agent, prepared)
        return None
    return prepared


def apply_prepared_branch_view(
    agent: Any, prepared: dict[str, Any], *, candidate: Conversation | None = None
) -> None:
    """Install a prepared subtree onto the live agent."""
    messages = prepared["messages"]
    selected = prepared["selected"]
    conversation = agent.controller.conversation
    if candidate is None:
        candidate = build_branch_conversation(conversation, messages)
    agent._context_cutoff_event_id = prepared.get("context_cutoff_event_id", 0)
    manager = getattr(agent, "compact_manager", None)
    if manager is not None:
        state = prepared.get("compact_state") or replayed_compact_state(messages)
        manager._compact_count = state["compact_count"]
        manager._last_compact_time = state["last_compact_time"]
    conversation.adopt_contents(candidate)

    controller = agent.controller
    config = getattr(controller, "config", None)
    if config is not None and getattr(config, "elide_tool_results", False):
        compact = getattr(agent, "compact_manager", None)
        compact_max = (
            compact.config.max_tokens
            if compact is not None
            and compact.config.enabled
            and getattr(compact.config, "max_tokens", 0)
            else 0
        )
        if compact_max and estimate_tokens(conversation) >= int(
            compact_max * compact.config.threshold
        ):
            elide_stale_tool_results(conversation)

    if selected:
        max_turn = max(selected)
        agent._turn_index = max_turn
        agent._branch_id = selected[max_turn]
        agent._parent_branch_path = [
            (turn, branch)
            for turn, branch in sorted(selected.items())
            if 0 < turn < max_turn and branch > 0
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
