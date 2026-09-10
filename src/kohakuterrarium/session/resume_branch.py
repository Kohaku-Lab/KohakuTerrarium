"""Branch-matching helpers for resume (kept small for the file-size guard).

Split out of :mod:`resume` so it stays under the line limit. These helpers
decide whether a saved conversation snapshot still matches the branch the
agent resumes onto, or must be rebuilt from the event log.
"""

from typing import Any

from kohakuterrarium.core.conversation_elide import TOOL_FEEDBACK_KIND
from kohakuterrarium.session.history import (
    normalize_resumable_events,
    replay_conversation,
    select_live_event_ids,
)


def snapshot_has_turn_metadata(snapshot: list[dict]) -> bool:
    """Return whether the snapshot carries valid user turn metadata.

    Edit/regenerate targeting resolves the edited turn through message
    metadata; legacy snapshots saved without it force content matching,
    which is ambiguous when a turn's wording repeats. Such snapshots are
    backfilled from the event log so targeting stays deterministic.

    A metadata entry is only "valid" when ``turn_index`` and ``branch_id``
    are positive ints — the exact shape every writer produces (backfill,
    replay, live snapshot). A non-int (corrupt/legacy-typed) value would be
    trusted here but fail edit targeting, so it must trigger backfill too.
    A non-dict entry is malformed data that ``_build_conversation`` would
    crash on, so it must also trigger backfill/replay rather than being
    trusted.
    """
    return all(
        isinstance(m, dict)
        and (
            m.get("role") != "user"
            or (
                isinstance(m.get("metadata"), dict)
                and m["metadata"].get("kind") == TOOL_FEEDBACK_KIND
            )
            or (
                isinstance(m.get("metadata"), dict)
                and isinstance(m["metadata"].get("turn_index"), int)
                and m["metadata"]["turn_index"] > 0
                and isinstance(m["metadata"].get("branch_id"), int)
                and m["metadata"]["branch_id"] > 0
            )
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
    pos_by_key: dict[tuple[int, int], int] = {}
    type_by_key: dict[tuple[int, int], str] = {}
    for evt in events:
        if evt.get("type") not in ("user_message", "user_input"):
            continue
        if isinstance(evt.get("event_id"), int) and evt["event_id"] not in live_ids:
            continue
        ti = evt.get("turn_index")
        bi = evt.get("branch_id")
        if not isinstance(ti, int) or not isinstance(bi, int):
            continue
        key = (ti, bi)
        etype = evt.get("type", "")
        meta = {
            # Only a user_message carries an editable event_id; a legacy
            # user_input's id is NOT valid for edit targeting
            # (select_raw_history_prefix rejects non-user_message targets),
            # so leave it unset when the chosen source is user_input.
            "event_id": evt.get("event_id") if etype == "user_message" else None,
            "turn_index": ti,
            "branch_id": bi,
        }
        if key in pos_by_key:
            # A turn usually carries BOTH user_input (written first) and
            # user_message (the canonical editable event). Edit targeting
            # requires event_id to point at a user_message, so if we recorded
            # a user_input first, replace it with the user_message event_id.
            # A duplicate user_message must NOT overwrite the first one.
            if etype == "user_message" and type_by_key[key] != "user_message":
                meta_by_pos[pos_by_key[key]] = meta
                type_by_key[key] = etype
            continue
        type_by_key[key] = etype
        pos_by_key[key] = len(meta_by_pos)
        meta_by_pos.append(meta)
    user_messages = [
        m
        for m in snapshot
        if isinstance(m, dict)
        and m.get("role") == "user"
        and not (
            isinstance(m.get("metadata"), dict)
            and m["metadata"].get("kind") == TOOL_FEEDBACK_KIND
        )
    ]
    tail_meta = meta_by_pos[-len(user_messages) :] if user_messages else []
    out: list[dict] = []
    for msg in snapshot:
        if not isinstance(msg, dict):
            # Malformed snapshot entry (corrupt data): keep it verbatim so
            # backfill never crashes here; resume's downstream build drops or
            # tolerates it rather than trusting a broken message shape.
            out.append(msg)
            continue
        m = dict(msg)
        raw_metadata = m.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        is_canonical_user = (
            m.get("role") == "user" and metadata.get("kind") != TOOL_FEEDBACK_KIND
        )
        if is_canonical_user and tail_meta:
            meta = dict(metadata)
            # Position-aligned: every user message consumes one tail slot so
            # later messages keep their correct turn. Replace the canonical
            # identity fields whenever turn or branch is not a positive int
            # (missing, corrupt, or legacy-typed) — a partial/valid-looking
            # value must not survive and later fail edit targeting.
            ti = meta.get("turn_index")
            bi = meta.get("branch_id")
            if not (isinstance(ti, int) and ti > 0 and isinstance(bi, int) and bi > 0):
                meta["turn_index"] = tail_meta[0]["turn_index"]
                meta["branch_id"] = tail_meta[0]["branch_id"]
                if tail_meta[0].get("event_id") is not None:
                    meta["event_id"] = tail_meta[0]["event_id"]
                else:
                    meta.pop("event_id", None)
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
    # An agent with no live selection (core code can set turn/branch to 0) has
    # no branch to compare against — treat it as "no mismatch" and trust the
    # snapshot rather than triggering an unnecessary replay.
    if not isinstance(a_ti, int) or not isinstance(a_bi, int) or a_ti <= 0 or a_bi <= 0:
        return False
    snapshot_path = _safe_branch_path(branch.get("parent_branch_path")) + [(ti, bi)]
    agent_path = _safe_branch_path(a_ppath) + [(a_ti, a_bi)]
    return not is_path_prefix(snapshot_path, agent_path)


def replayed_messages_for(
    store: Any,
    agent_name: str,
    events: list[dict] | None = None,
) -> list[dict]:
    """Replay the latest live subtree from the event log (branch-aware).

    Used by resume when the saved snapshot belongs to a different branch.
    ``events`` is the caller's shared raw read; without it the store is
    read here.
    """
    if events is None:
        try:
            events = list(store.get_events(agent_name))
        except Exception:  # pragma: no cover - defensive
            return []
    if not events:
        return []
    return replay_conversation(
        normalize_resumable_events(events), include_metadata=True
    )
