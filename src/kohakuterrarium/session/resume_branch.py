"""Branch-matching helpers for resume (kept small for the file-size guard).

Split out of :mod:`resume` so it stays under the line limit. These helpers
decide whether a saved conversation snapshot still matches the branch the
agent resumes onto, or must be rebuilt from the event log.
"""

from typing import Any

from kohakuterrarium.session.history import (
    normalize_resumable_events,
    replay_conversation,
    select_live_event_ids,
)


def snapshot_has_turn_metadata(snapshot: list[dict]) -> bool:
    """Return whether the snapshot carries user turn metadata.

    Edit/regenerate targeting resolves the edited turn through message
    metadata; legacy snapshots saved without it force content matching,
    which is ambiguous when a turn's wording repeats. Such snapshots are
    backfilled from the event log so targeting stays deterministic.
    """
    return all(
        not isinstance(m, dict)
        or m.get("role") != "user"
        or (
            isinstance(m.get("metadata"), dict)
            and m["metadata"].get("turn_index") is not None
        )
        for m in snapshot
    )


def backfill_turn_metadata(snapshot: list[dict], events: list[dict]) -> list[dict]:
    """Backfill user-turn metadata onto a legacy metadata-less snapshot.

    The snapshot is the canonical persisted state (compaction exists only
    there), so it is trusted verbatim: a full replay would resurrect the
    pre-compact history and drop snapshot-only in-flight messages. Turn
    identity is recovered from the live ``user_message`` events so edit
    targeting stays deterministic.

    Snapshot user messages hold the most recent turns verbatim (compaction
    summarizes the prefix and keeps only the live zone), so they map to the
    LAST ``user_message`` events, not the first ones.
    """
    live_ids = set(select_live_event_ids(events))
    meta_by_pos: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for evt in events:
        if evt.get("type") not in ("user_message", "user_input"):
            continue
        if isinstance(evt.get("event_id"), int) and evt["event_id"] not in live_ids:
            continue
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        # Deduplicate per (turn, branch): a merged/migrated store can carry
        # duplicate user events that would otherwise mis-align the tail map.
        if isinstance(ti, int) and isinstance(bi, int) and (ti, bi) not in seen:
            seen.add((ti, bi))
            meta_by_pos.append(
                {
                    # Same shape as replay(include_metadata=True) so legacy
                    # snapshots and replay-built views carry identical user
                    # turn identity (event_id included).
                    "event_id": evt.get("event_id"),
                    "turn_index": ti,
                    "branch_id": bi,
                }
            )
    user_messages = [m for m in snapshot if m.get("role") == "user"]
    tail_meta = meta_by_pos[-len(user_messages) :] if user_messages else []
    out: list[dict] = []
    for msg in snapshot:
        m = dict(msg)
        if m.get("role") == "user" and tail_meta:
            meta = dict(m.get("metadata") or {})
            # Position-aligned: every user message consumes one tail slot so
            # later messages keep their correct turn; an existing metadata is
            # preserved (never overwritten).
            if meta.get("turn_index") is None:
                meta.setdefault("event_id", tail_meta[0]["event_id"])
                meta.setdefault("turn_index", tail_meta[0]["turn_index"])
                meta.setdefault("branch_id", tail_meta[0]["branch_id"])
                m["metadata"] = meta
            tail_meta = tail_meta[1:]
        out.append(m)
    return out


def is_path_prefix(sub: list[tuple[int, int]], full: list[tuple[int, int]]) -> bool:
    """Whether ``sub`` is a strict/equal prefix of ``full``."""
    return len(sub) <= len(full) and full[: len(sub)] == sub


def _safe_branch_path(raw: Any) -> list[tuple[int, int]]:
    """Parse a persisted branch path defensively.

    Malformed entries (int, string, wrong length, non-int coords) are
    skipped so resume never crashes on corrupt state.
    """
    out: list[tuple[int, int]] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            t, b = int(item[0]), int(item[1])
        except (TypeError, ValueError):
            continue
        out.append((t, b))
    return out


def snapshot_mismatches_branch(store: Any, agent: Any, agent_name: str) -> bool:
    """Whether the saved snapshot belongs to a different branch than the one
    resume lands on (the latest live subtree, restored by
    ``_restore_turn_branch_state``).

    A snapshot tagged with a branch that is an ANCESTOR of the target branch
    is still usable — resume appends the post-snapshot tail. Only a snapshot
    whose path diverges (a sibling branch) must be discarded and rebuilt.
    """
    try:
        branch = store.state.get(f"{agent_name}:snapshot_branch")
    except (KeyError, TypeError):
        return False
    if not isinstance(branch, dict):
        return False  # legacy snapshot without a tag -> trust it
    ti = branch.get("turn_index")
    bi = branch.get("branch_id")
    if not isinstance(ti, int) or not isinstance(bi, int) or ti <= 0 or bi <= 0:
        return False
    a_ti = getattr(agent, "_turn_index", None)
    a_bi = getattr(agent, "_branch_id", None)
    a_ppath = getattr(agent, "_parent_branch_path", None) or []
    if not isinstance(a_ti, int) or not isinstance(a_bi, int):
        return False
    snapshot_path = _safe_branch_path(branch.get("parent_branch_path")) + [(ti, bi)]
    agent_path = _safe_branch_path(a_ppath) + [(a_ti, a_bi)]
    return not is_path_prefix(snapshot_path, agent_path)


def replayed_messages_for(store: Any, agent_name: str) -> list[dict]:
    """Replay the latest live subtree from the event log (branch-aware).

    Used by resume when the saved snapshot belongs to a different branch.
    """
    try:
        events = list(store.get_events(agent_name))
    except Exception:  # pragma: no cover - defensive
        return []
    if not events:
        return []
    return replay_conversation(
        normalize_resumable_events(events), include_metadata=True
    )
