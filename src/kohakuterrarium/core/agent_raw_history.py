"""Raw persisted-history helpers for message branch mutations."""

from dataclasses import dataclass
from typing import Any, Protocol

from kohakuterrarium.core.agent_message_history import (
    apply_prepared_branch_view,
    reload_conversation_under_branch_view,
    replay_with_settled_tools,
    resolve_edit_message_index,
    snapshot_needs_settled_tool_replay,
    turn_index_for_user_position,
)
from kohakuterrarium.core.branch_continuation import live_branch_path
from kohakuterrarium.core.branch_conversation import build_branch_conversation
from kohakuterrarium.core.compact_branch import (
    compact_rule_from_event,
    compact_source_path_from_event,
    is_summary_message,
)
from kohakuterrarium.core.settled_refresh import prepare_mutation_prefix
from kohakuterrarium.core.history_candidate import (
    HistoryCandidate,
    adopt_candidate_conversation,
    detached_conversation,
    final_prompt_messages,
    install_candidate_state,
    require_history_candidate,
    without_retention,
)
from kohakuterrarium.core.snapshot_history import (
    prepare_snapshot,
    replayed_compact_state,
)
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.llm.message import dicts_to_messages
from kohakuterrarium.session.history import (
    compact_covered_event_ids,
    normalize_tool_call_events,
    replay_conversation,
)
from kohakuterrarium.session.raw_history import (
    UserMessageSelector,
    select_raw_history_prefix,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


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


def canonical_target_event(
    agent: RawHistoryAgent,
    turn_index: int,
    branch_id: int,
) -> UserMessageSelector:
    """Return the one persisted ``user_message`` rooting a turn/branch."""
    if agent.session_store is None:
        raise ConflictError(
            "Cannot reconstruct summarized history without a persisted target"
        )
    targets = [
        evt
        for evt in agent.session_store.get_events(agent.config.name)
        if evt.get("type") == "user_message"
        and evt.get("turn_index") == turn_index
        and evt.get("branch_id") == branch_id
    ]
    if len(targets) != 1:
        raise ConflictError("Cannot identify the summarized turn to mutate")
    return UserMessageSelector(targets[0]["event_id"], turn_index, branch_id)


def _prefix_messages(
    agent: RawHistoryAgent,
    all_events: list[dict[str, Any]],
    prefix,
    target: UserMessageSelector,
    *,
    prefer_checkpoint: bool,
    skip_watermarks: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[Any], dict[str, Any] | None]:
    """Detached message list for one target prefix and its compact baseline."""
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
    prepared = (
        prepare_snapshot(
            agent.session_store,
            agent.config.name,
            all_events,
            prefix.branch_view,
            replay=replay_with_settled_tools,
            needs_replay=snapshot_needs_settled_tool_replay,
            before_turn=target.turn_index,
            skip_watermarks=skip_watermarks,
        )
        if prefer_checkpoint
        else None
    )
    if prepared is not None:
        messages = prepared["messages"] + replay_conversation(
            [prefix.target], include_metadata=True
        )
    else:
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
        messages = replay_conversation(
            normalize_tool_call_events(
                raw_events,
                covered_event_ids=compact_covered_event_ids(
                    raw_events, prefix.branch_view
                ),
            ),
            branch_view=prefix.branch_view,
            include_metadata=True,
        )
    persisted = dicts_to_messages(messages)
    for raw_message, message in zip(messages, persisted):
        metadata = raw_message.get("metadata")
        if isinstance(metadata, dict):
            message.metadata = dict(metadata)
    if not any(message.role == "system" for message in persisted):
        persisted = [
            *(
                message
                for message in agent.controller.conversation.get_messages()
                if message.role == "system"
            ),
            *persisted,
        ]
    return messages, persisted, prepared


def _candidate_state(
    agent: RawHistoryAgent,
    target: UserMessageSelector,
    prefix,
    messages: list[Any],
    prepared: dict[str, Any] | None,
    raw_messages: list[dict[str, Any]],
) -> HistoryCandidate:
    """Bundle one detached reconstruction with the state it would install."""
    state = (
        prepared["compact_state"] if prepared else replayed_compact_state(raw_messages)
    )
    return HistoryCandidate(
        messages=messages,
        turn_index=target.turn_index,
        branch_id=target.branch_id,
        parent_branch_path=[
            (turn, branch)
            for turn, branch in prefix.branch_view.items()
            if 0 < turn < target.turn_index and branch > 0
        ],
        compact_state=state,
        context_cutoff_event_id=(
            prepared.get("context_cutoff_event_id", 0) if prepared else 0
        ),
        target_index=len(messages) - 1,
    )


def prepare_raw_prefix_candidate(
    agent: RawHistoryAgent,
    target: UserMessageSelector,
    *,
    branch_view: dict[int, int] | None = None,
    replacement_content: Any = None,
) -> HistoryCandidate:
    """Build and admit a target prefix without changing agent state.

    Prefer proven checkpoints over raw reconstruction. Alternatives to a
    rejected cutoff-bearing candidate must preserve its exact cutoff.
    Every candidate remains subject to the configured context and retention
    budgets before installation.
    """
    if agent.session_store is None:
        raise ValueError("raw persisted history is unavailable")
    all_events = list(agent.session_store.get_events(agent.config.name))
    prefix = select_raw_history_prefix(
        all_events, selector=target, branch_view=branch_view
    )
    failures: list[ConflictError] = []
    accepted: HistoryCandidate | None = None
    skipped: set[int] = set()
    raw_attempt: tuple | None = None
    cutoff_refused = False
    # Cutoff a rejected candidate still has to preserve in its replacement.
    required_cutoff = 0
    remaining = (
        len(
            getattr(agent.session_store, "state", {}).get(
                f"{agent.config.name}:branch_snapshots", []
            )
        )
        + 1
    )
    while accepted is None and remaining > 0:
        remaining -= 1
        attempt = _prefix_messages(
            agent,
            all_events,
            prefix,
            target,
            prefer_checkpoint=True,
            skip_watermarks=skipped or None,
        )
        prepared = attempt[2]
        if prepared is None:
            raw_attempt = attempt
            break
        candidate = _candidate_state(
            agent, target, prefix, attempt[1], prepared, attempt[0]
        )
        if required_cutoff and candidate.context_cutoff_event_id != required_cutoff:
            cutoff_refused = True
            break
        try:
            require_history_candidate(
                agent, _admit_payload(candidate, replacement_content)
            )
        except ConflictError as exc:
            failures.append(exc)
            kept = prepared.get("watermark")
            if kept is None:
                cutoff_refused = True
                break
            if candidate.context_cutoff_event_id:
                required_cutoff = candidate.context_cutoff_event_id
            skipped.add(kept)
            continue
        accepted = candidate
    if accepted is None and not cutoff_refused and not required_cutoff:
        if raw_attempt is None:
            raw_attempt = _prefix_messages(
                agent, all_events, prefix, target, prefer_checkpoint=False
            )
        candidate = _candidate_state(
            agent, target, prefix, raw_attempt[1], None, raw_attempt[0]
        )
        try:
            require_history_candidate(
                agent, _admit_payload(candidate, replacement_content)
            )
        except ConflictError as exc:
            failures.append(exc)
        else:
            accepted = candidate
    if accepted is None:
        raise failures[0]
    return accepted


def _admit_payload(candidate: HistoryCandidate, replacement_content: Any) -> list[Any]:
    """The prompt admission measures for one reconstructed candidate."""
    if replacement_content is None:
        return candidate.messages
    return final_prompt_messages(
        candidate.messages, candidate.target_index, replacement_content
    )


def prepare_canonical_raw_candidate(
    agent: RawHistoryAgent,
    turn_index: int | None,
    branch_id: int,
    *,
    branch_view: dict[int, int] | None = None,
    replacement_content: Any = None,
) -> HistoryCandidate:
    """Detached raw reconstruction for the canonical event of one turn/branch."""
    if agent.session_store is None or turn_index is None:
        raise ConflictError(
            "Cannot reconstruct summarized history without a persisted target"
        )
    return prepare_raw_prefix_candidate(
        agent,
        canonical_target_event(agent, turn_index, branch_id),
        branch_view=branch_view,
        replacement_content=replacement_content,
    )


def install_history_candidate(
    agent: RawHistoryAgent, candidate: HistoryCandidate
) -> None:
    """Install an admitted candidate's messages and state."""
    adopt_candidate_conversation(
        agent, detached_conversation(agent.controller.conversation, candidate.messages)
    )
    install_candidate_state(agent, candidate)


def prepare_branch_view_candidate(
    agent: RawHistoryAgent,
    branch_view: dict[int, int],
) -> dict[str, Any]:
    """Detached replay of a selected subtree; nothing is installed."""
    if agent.session_store is None:
        raise ValueError("raw persisted history is unavailable")
    events = list(agent.session_store.get_events(agent.config.name))
    prepared = reload_conversation_under_branch_view(
        agent,
        branch_view,
        events=events,
        apply=False,
    )
    conversation = build_branch_conversation(
        without_retention(agent.controller.conversation), prepared["messages"]
    )
    return {"prepared": prepared, "conversation": conversation}


@dataclass(slots=True)
class PreparedEdit:
    """Admitted edit target, ready to install without further reconstruction."""

    index: int
    turn_index: int | None
    user_position: int
    candidate: HistoryCandidate | None = None
    branch_candidate: dict[str, Any] | None = None

    def install(self, agent: RawHistoryAgent) -> None:
        """Install the admitted reconstruction, leaving an unchanged prefix in place."""
        if self.candidate is not None:
            install_history_candidate(agent, self.candidate)
        elif self.branch_candidate is not None:
            apply_prepared_branch_view(
                agent,
                self.branch_candidate["prepared"],
                candidate=self.branch_candidate["conversation"],
            )


def summary_inputs_changed(messages: list[Any], turn_index: int | None) -> bool:
    """Whether a live summary still depends on the mutation target's inputs."""
    return any(
        (inputs := compact_source_path_from_event(msg.metadata)) is None
        or turn_index is None
        or any(turn >= turn_index for turn, _ in inputs)
        for msg in messages
        if is_summary_message(msg)
    )


def prepare_persisted_edit(
    agent: RawHistoryAgent,
    *,
    message_idx: int,
    replacement_content: Any,
    turn_index: int | None = None,
    user_position: int | None = None,
    branch_view: dict[int, int] | None = None,
    target: UserMessageSelector | None = None,
) -> PreparedEdit | None:
    """Resolve, reconstruct and admit an edit target without changing state.

    Every persisted route — a canonical ``target``, a ``branch_view`` replay or
    a summary-dependent in-memory prefix — produces a detached candidate here.
    The prompt the rerun would send (candidate prefix plus the edited input) is
    admitted before the caller installs or truncates anything, so a rejection
    cannot leave a half-switched branch, bumped compact counters or dropped
    messages behind.
    """
    candidate = None
    branch_candidate = None
    if target is not None:
        candidate = prepare_raw_prefix_candidate(
            agent,
            target,
            branch_view=branch_view,
            replacement_content=replacement_content,
        )
        messages = candidate.messages
        turn_index = target.turn_index
        user_position = None
    elif branch_view and agent.session_store is not None:
        branch_candidate = prepare_branch_view_candidate(agent, branch_view)
        messages = branch_candidate["conversation"].get_messages()
    else:
        messages = agent.controller.conversation.get_messages()
    index = resolve_edit_message_index(
        agent,
        messages,
        message_idx,
        turn_index=turn_index,
        user_position=user_position,
        branch_view=branch_view,
    )
    if index is None:
        logger.warning(
            "Invalid edit target",
            index=message_idx,
            turn_index=turn_index,
            user_position=user_position,
        )
        return None
    refreshed = False
    if candidate is None and branch_candidate is None:
        branch_candidate = prepare_mutation_prefix(agent, index, replacement_content)
        if branch_candidate is not None:
            refreshed = True
            index = branch_candidate["target_index"]
            messages = branch_candidate["conversation"].get_messages()
    edited = messages[index]
    if edited.role != "user":
        logger.warning("Can only edit user messages", role=edited.role)
        return None
    resolved_user_position = (
        sum(1 for message in messages[: index + 1] if message.role == "user") - 1
    )
    resolved_turn = turn_index or edited.metadata.get("turn_index")
    if resolved_turn is None:
        if any(is_summary_message(message) for message in messages):
            raise ConflictError(
                "Cannot identify an untagged target in summarized history"
            )
        resolved_turn = turn_index_for_user_position(
            agent, resolved_user_position, branch_view=branch_view
        )
    if (
        candidate is None
        and not refreshed
        and summary_inputs_changed(messages, resolved_turn)
    ):
        # A summary still depends on the inputs being replaced: reconstruct the
        # target prefix instead of splicing into that summary.
        candidate = prepare_canonical_raw_candidate(
            agent,
            resolved_turn,
            edited.metadata.get("branch_id")
            or (branch_view or {}).get(resolved_turn, agent._branch_id),
            branch_view=branch_view or live_branch_path(agent),
            replacement_content=replacement_content,
        )
        branch_candidate = None
        messages = candidate.messages
        index = candidate.target_index
        resolved_turn = candidate.turn_index
    if candidate is not None or branch_candidate is not None:
        require_history_candidate(
            agent, final_prompt_messages(messages, index, replacement_content)
        )
    return PreparedEdit(
        index=index,
        turn_index=resolved_turn,
        user_position=resolved_user_position,
        candidate=candidate,
        branch_candidate=branch_candidate,
    )
