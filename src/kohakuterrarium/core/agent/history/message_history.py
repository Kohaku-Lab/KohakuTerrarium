"""Branch-aware history lookup helpers for agent message mutations."""

from collections.abc import Callable
from typing import Any

from kohakuterrarium.core.conversation.branch import build_branch_conversation
from kohakuterrarium.core.conversation.conversation import Conversation
from kohakuterrarium.core.conversation.elide import (
    elide_stale_tool_results,
    estimate_tokens,
)
from kohakuterrarium.core.conversation.locator import (
    user_message_indices_for_content,
    user_message_indices_for_turn,
)
from kohakuterrarium.core.conversation.settled_identity import (
    pending_snapshot_jobs,
    result_identities,
    result_is_final,
)
from kohakuterrarium.core.conversation.snapshot import (
    prepare_snapshot,
    replayed_compact_state,
)
from kohakuterrarium.session.history.replay import (
    compact_covered_event_ids,
    index_parent_paths,
    normalize_resumable_events,
    normalize_tool_call_events,
    replay_conversation,
    resolve_branch_view_strict,
    project_live_event_stream,
    select_live_event_ids,
    select_live_event_positions,
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
    seen_turns: set[int] = set()
    turns: list[tuple[int, int]] = []
    for event in project_live_event_stream(events, branch_view=branch_view):
        if event.get("type") != "user_message":
            continue
        event_id = event.get("event_id")
        turn_index = event.get("turn_index")
        if not isinstance(event_id, int) or not isinstance(turn_index, int):
            continue
        if turn_index in seen_turns:
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
    for event in project_live_event_stream(events, branch_view=selected):
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
    live_positions: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Attach completed results to retained calls without rewriting settled pairs.

    Position-aware Replay callers supply live_positions so mirrored events
    with reused logical IDs cannot borrow each other's tool-call ownership.
    The ID-set fallback preserves the historical low-level API contract.
    """
    if live_positions is None:
        live_events = [
            event
            for event in events
            if not isinstance(event.get("event_id"), int)
            or event["event_id"] in live_event_ids
        ]
    else:
        live_events = [
            event for index, event in enumerate(events) if index in live_positions
        ]
    identities = {id(event): _tool_identity(event) for event in live_events}
    live_call_turns: dict[str, Any] = {}
    live_call_provider_ids: dict[str, Any] = {}
    for event in live_events:
        if event.get("type") == "tool_call" and (identity := identities[id(event)]):
            if identity not in live_call_turns:
                live_call_turns[identity] = event.get("turn_index")
                live_call_provider_ids[identity] = event.get("tool_call_id")
    live_result_ids: set[str] = {
        identity
        for event in live_events
        if event.get("type") == "tool_result" and (identity := identities[id(event)])
    }
    all_call_counts: dict[str, int] = {}
    for event in events:
        if event.get("type") == "tool_call" and (identity := _tool_identity(event)):
            all_call_counts[identity] = all_call_counts.get(identity, 0) + 1
    late_results: dict[str, dict[str, Any]] = {}
    for position, event in enumerate(events):
        if event.get("type") != "tool_result":
            continue
        identity = _tool_identity(event)
        eid = event.get("event_id")
        physically_live = (
            position in live_positions
            if live_positions is not None
            else isinstance(eid, int) and eid in live_event_ids
        )
        if eid in (covered_event_ids or ()):
            continue
        if (
            identity
            and all_call_counts.get(identity) == 1
            and identity in live_call_turns
            and identity not in live_result_ids
            and (
                event.get("tool_call_id") is None
                or event.get("tool_call_id") == live_call_provider_ids.get(identity)
            )
            and identity not in late_results
        ):
            projected = dict(event)
            projected["_source_event_id"] = projected.pop("event_id", None)
            projected["_settled_from_call"] = True
            late_results[identity] = projected
        elif (
            identity
            and identity in live_call_turns
            and physically_live
            and (
                event.get("tool_call_id") is None
                or event.get("tool_call_id") == live_call_provider_ids.get(identity)
            )
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
    identity_counts: dict[str, tuple[int, int]] = {}
    for event in live_events:
        etype = event.get("type")
        if etype not in {"tool_call", "tool_result"}:
            continue
        identity = identities[id(event)]
        calls, results = identity_counts.get(identity, (0, 0))
        identity_counts[identity] = (
            calls + int(etype == "tool_call"),
            results + int(etype == "tool_result"),
        )
    rounds: dict[int, int] = {}
    round_index = 0
    boundary_index = 0
    boundaries: dict[int, int] = {}
    call_info: dict[str, tuple[int, int, str]] = {}
    for index, event in enumerate(live_events):
        if event.get("type") == "assistant_round_start":
            round_index += 1
        if event.get("type") in {
            "user_message",
            "user_input_injected",
            "compact_complete",
            "compact_replace",
        }:
            boundary_index += 1
        rounds[index] = round_index
        boundaries[index] = boundary_index
        if event.get("type") == "tool_call":
            identity = identities[id(event)]
            if identity and identity_counts.get(identity) == (1, 1):
                call_info[identity] = (
                    index,
                    round_index,
                    str(event.get("tool_call_id") or identity),
                )
    provider_owners: dict[tuple[int, str], set[str]] = {}
    for index, event in enumerate(live_events):
        if event.get("type") == "tool_call":
            identity = identities[id(event)]
            provider_id = str(event.get("tool_call_id") or identity)
            provider_owners.setdefault((boundaries[index], provider_id), set()).add(
                identity
            )
    candidates: dict[str, int] = {}
    for index, event in enumerate(live_events):
        if event.get("type") != "tool_result" or event.get("event_id") in (
            covered_event_ids or ()
        ):
            continue
        identity = identities[id(event)]
        call = call_info.get(identity)
        if (
            call is None
            or identity in late_results
            or len(provider_owners[(boundaries[call[0]], call[2])]) != 1
        ):
            continue
        call_index, call_round, _ = call
        result_provider_id = event.get("tool_call_id")
        call_provider_id = live_events[call_index].get("tool_call_id")
        if result_provider_id is not None and result_provider_id != call_provider_id:
            continue
        if (
            index > call_index
            and boundaries[index] == boundaries[call_index]
            and rounds[index] > call_round
            and event.get("turn_index") == live_events[call_index].get("turn_index")
            and isinstance(event.get("turn_index"), int)
        ):
            candidates[identity] = index
    starts: dict[int, list[str]] = {}
    for identity in candidates:
        starts.setdefault(call_info[identity][0], []).append(identity)
    anchor_for: dict[str, int] = {}
    invalid: set[str] = set()
    pending: dict[str, str] = {}
    call_group: list[int] = []
    for index, event in enumerate(live_events):
        etype = event.get("type")
        canonical = event.get("message") if etype == "assistant_message" else None
        is_announcement = etype == "assistant_tool_calls" or (
            isinstance(canonical, dict) and canonical.get("tool_calls")
        )
        if etype == "tool_call":
            call_group.append(index)
        elif etype == "subagent_call":
            pass
        elif not is_announcement:
            if pending and len(call_group) > 1:
                invalid.update(pending.values())
                pending.clear()
            if pending and etype not in {
                "assistant_round_start",
                "assistant_message",
                "plugin_hook_timing",
                "processing_start",
                "processing_end",
                "background_result",
                "tool_result",
                "user_message",
                "user_input_injected",
                "compact_complete",
                "compact_replace",
            }:
                invalid.update(pending.values())
                pending.clear()
            call_group.clear()
        for identity in starts.get(index, ()):
            pending[call_info[identity][2]] = identity
        if is_announcement and pending:
            calls = (
                canonical.get("tool_calls") if canonical else event.get("tool_calls")
            )
            announced = {str(call.get("id") or "") for call in calls or []}
            matched = announced & pending.keys()
            group_provider_ids = {
                str(
                    live_events[pos].get("tool_call_id")
                    or identities[id(live_events[pos])]
                )
                for pos in call_group
            }
            if not matched or (
                not pending.keys() <= announced or not group_provider_ids <= announced
            ):
                invalid.update(pending.values())
                pending.clear()
            else:
                for provider_id in matched:
                    identity = pending.pop(provider_id)
                    anchor_for[identity] = index
            call_group.clear()
        if (
            event.get("type")
            in {
                "assistant_round_start",
                "user_message",
                "user_input_injected",
                "compact_complete",
                "compact_replace",
            }
            and pending
        ):
            for identity in pending.values():
                anchor_for[identity] = call_info[identity][0]
            pending.clear()
    for identity in pending.values():
        anchor_for[identity] = call_info[identity][0]
    for identity in invalid:
        candidates.pop(identity, None)
    grouped: dict[int, list[dict[str, Any]]] = {}
    remove: set[int] = set()
    for identity, result_index in candidates.items():
        projected = dict(live_events[result_index])
        projected["_source_event_id"] = projected.pop("event_id", None)
        projected["_settled_from_call"] = True
        grouped.setdefault(anchor_for[identity], []).append(projected)
        remove.add(result_index)
    replay_events: list[dict[str, Any]] = []
    for index, event in enumerate(live_events):
        identity = identities[id(event)]
        if index in remove or (
            event.get("type") == "tool_result" and identity in late_results
        ):
            continue
        replay_events.append(event)
        if event.get("type") == "tool_call" and identity in late_results:
            replay_events.append(late_results[identity])
        replay_events.extend(grouped.get(index, ()))
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
        events,
        live_event_ids,
        covered_event_ids=covered_ids,
        live_positions=select_live_event_positions(events, branch_view=branch_view),
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
        events,
        live_ids,
        covered_event_ids=covered_ids,
        live_positions=select_live_event_positions(events, branch_view=branch_view),
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
        identities = result_identities(message)
        if not identities:
            continue
        if not result_is_final(message):
            continue
        for identity in identities:
            owners = alias_owners.get(identity, set())
            if len(owners) == 1:
                present.update(owners & needed.keys())
    return any(job_id not in present for job_id in needed)


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
            events,
            live_event_ids,
            covered_event_ids=covered_ids,
            live_positions=select_live_event_positions(events, branch_view=branch_view),
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
