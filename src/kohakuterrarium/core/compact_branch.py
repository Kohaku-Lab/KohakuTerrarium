"""Branch-aware compaction helpers (kept small for the file-size guard).

Split out of :mod:`compact` so the compact manager stays under the line
limit. These pure helpers turn the agent's branch state into the path a
compaction covers, and compute the event-id range from the event log.
"""

from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def current_path(agent: Any) -> set[tuple[int, int]]:
    """Expand the agent's branch path into a {(turn, branch)} set.

    The path starts at the oldest compactable turn and ends at the agent's
    current turn. It is what a compaction on this branch covers.
    """
    if agent is None:
        return set()
    path: set[tuple[int, int]] = set()
    raw = getattr(agent, "_parent_branch_path", None)
    if raw:
        for item in raw:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            try:
                t, b = int(item[0]), int(item[1])
            except (TypeError, ValueError):
                continue
            if t > 0:
                path.add((t, b))
    ti = getattr(agent, "_turn_index", None)
    bi = getattr(agent, "_branch_id", None)
    if isinstance(ti, int) and isinstance(bi, int) and ti > 0:
        path.add((ti, bi))
    return path


def persist_compacted(
    store: Any,
    agent_name: str,
    agent: Any,
    conversation: Any,
    compact_count: int,
    last_compact_time: float,
) -> None:
    """Persist the compacted conversation snapshot + compact state.

    The splice runs mid-turn: the in-flight tail announcement is preserved so
    resume can reconstruct the active round. The snapshot is tagged with the
    branch it was created on, and compact_count / watermark / cooldown are
    stored for resume continuity.
    """
    try:
        store.save_conversation(
            agent_name,
            conversation.to_messages(preserve_pending_tail=True, include_metadata=True),
        )
    except Exception as e:
        logger.warning(
            "Failed to save compacted conversation",
            error=str(e),
            exc_info=True,
        )
    tag_snapshot_branch(store, agent, agent_name)
    persist_compact_state(store, agent_name, compact_count, last_compact_time)


def persist_compact_state(
    store: Any, agent_name: str, compact_count: int, last_compact_time: float
) -> None:
    """Persist compact_count + snapshot watermark + cooldown timestamp.

    Restoring ``last_compact_time`` on resume keeps the cooldown window so a
    quick resume does not permit an immediate re-compact (audit finding 3j).
    """
    try:
        last_event_id = 0
        for evt in store.get_events(agent_name):
            eid = evt.get("event_id")
            if isinstance(eid, int) and eid > last_event_id:
                last_event_id = eid
        store.save_state(agent_name, compact_count=compact_count)
        store.state[f"{agent_name}:snapshot_event_id"] = last_event_id
        store.state[f"{agent_name}:last_compact_time"] = last_compact_time
    except Exception as e:
        logger.warning(
            "Failed to save compact_count state",
            error=str(e),
            exc_info=True,
        )


def emit_compact_skipped(
    router: Any, compact_count: int, reason: str, message: str
) -> None:
    """Terminal for a started round that did not complete. Every
    ``compact_start`` must be closed by exactly one terminal
    (``compact_complete`` or ``compact_skipped``) — FE / TUI / rich
    CLI all keep a "compacting…" indicator open until one lands."""
    if router is not None:
        router.notify_activity(
            "compact_skipped",
            message,
            metadata={
                "reason": reason,
                "round": compact_count + 1,
            },
        )


def tag_snapshot_branch(store: Any, agent: Any, agent_name: str) -> None:
    """Tag the saved snapshot with the branch it was created on.

    Mirrors ``session/output.py:on_processing_end`` so resume can match the
    target branch before trusting the snapshot. No-op when the agent has not
    started a turn or the store is read-only.
    """
    if agent is None or store is None:
        return
    ti = getattr(agent, "_turn_index", None)
    bi = getattr(agent, "_branch_id", None)
    if not isinstance(ti, int) or ti <= 0:
        return
    try:
        store.state[f"{agent_name}:snapshot_branch"] = {
            "turn_index": ti,
            "branch_id": bi,
            "parent_branch_path": getattr(agent, "_parent_branch_path", None),
        }
    except Exception:  # pragma: no cover - defensive
        pass


def build_compact_metadata(
    agent: Any,
    events: list[dict],
    keep_count: int,
    *,
    compact_round: int,
    summary: str,
    messages_compacted: int,
) -> dict:
    """Build ``compact_complete`` metadata with branch-aware coverage info.

    Attaches the replaced id range and the compacted branch path so replay
    can restore this branch's compacted view (and never swallow a sibling
    branch's events).
    """
    compact_path = current_path(agent)
    replaced = (
        compute_replaced_range(events, keep_count, compact_path) if events else None
    )
    metadata = {
        "round": compact_round,
        "summary": summary,
        "messages_compacted": messages_compacted,
    }
    if replaced is not None:
        metadata["replaced_from_event_id"] = replaced[0]
        metadata["replaced_to_event_id"] = replaced[1]
    if compact_path:
        metadata["compact_path"] = sorted([t, b] for (t, b) in compact_path)
    if agent is not None:
        metadata["turn_index"] = getattr(agent, "_turn_index", None)
        metadata["branch_id"] = getattr(agent, "_branch_id", None)
        metadata["parent_branch_path"] = getattr(agent, "_parent_branch_path", None)
    return metadata


def compute_replaced_range(
    events: list[dict],
    keep_count: int,
    path: set[tuple[int, int]],
) -> tuple[int, int] | None:
    """Event id range a compaction covers, computed from the event log.

    Only events on the agent's current branch ``path`` are considered;
    the live zone keeps the last ``keep_count`` user turns of that path,
    so everything older is replaced by the summary.
    """
    if not path:
        return None
    # Deduplicate by turn so a duplicated user_message event (graph merge /
    # migration) cannot shift the "first live" turn and over-replace.
    user_turns: list[int] = []
    seen_turns: set[int] = set()
    for evt in events:
        if evt.get("type") != "user_message":
            continue
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        if (
            isinstance(ti, int)
            and isinstance(bi, int)
            and (ti, bi) in path
            and ti not in seen_turns
        ):
            seen_turns.add(ti)
            user_turns.append(ti)
    if len(user_turns) <= keep_count:
        return None
    first_live = user_turns[-keep_count] if keep_count > 0 else None
    ids: list[int] = []
    for evt in events:
        eid = evt.get("event_id")
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        if (
            not isinstance(eid, int)
            or not isinstance(ti, int)
            or not isinstance(bi, int)
        ):
            continue
        if (ti, bi) not in path:
            continue
        if first_live is not None and ti >= first_live:
            continue
        ids.append(eid)
    if not ids:
        return None
    return min(ids), max(ids)
