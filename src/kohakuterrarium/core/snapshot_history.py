"""Select compatible conversation checkpoints and reconstruct their live suffix."""

import re
from copy import deepcopy
from typing import Any, Callable

from kohakuterrarium.core.compact_branch import compact_source_path_from_event
from kohakuterrarium.core.snapshot_repair import (
    place_repaired_messages,
    repair_gap_messages,
)
from kohakuterrarium.core.snapshot_prefix_proof import (
    INVALID as INVALID_PROOF,
    SummaryProof,
    ancestor_path,
    prove_unknown_summary,
    selected_target_event_id,
)
from kohakuterrarium.session.history import (
    compact_covered_event_ids,
    select_live_event_ids,
)
from kohakuterrarium.session.snapshot import load_records

# Sentinel for a record whose lineage contradicts the selected history. Such a
# record must be dropped outright: it is not a "cache miss" that the raw-event
# fallback may silently repair from.
_INVALID_PROOF = INVALID_PROOF
_INVALID_PREFIX = object()


def replayed_compact_state(messages: list[dict]) -> dict:
    """Recover compact numbering from the selected representation, without cooldown."""
    rounds = [
        int(match.group(1))
        for message in messages
        if message.get("role") == "assistant"
        and isinstance(message.get("content"), str)
        and (
            match := re.match(
                r"\[Previous context summary \(compact round (\d+)\)\]",
                message["content"],
            )
        )
    ]
    return {"compact_count": max(rounds, default=0), "last_compact_time": 0.0}


def _fresh(store: Any, namespace: str, events: list[dict], watermark: int) -> bool:
    latest = max((event.get("event_id", 0) for event in events), default=0)
    if watermark <= latest:
        return True
    getter = getattr(store, "get_all_events", None)
    if not callable(getter):
        return False
    return any(
        not key.startswith(f"{namespace}:e") and event.get("event_id", 0) >= watermark
        for key, event in getter()
    )


def _proof_inputs(
    record: dict,
    selected: dict[int, int],
    events: list[dict],
    live_ids: set[int],
    before_turn: int | None,
) -> dict | None | object:
    """Inputs proving this record's unknown-source summaries, or ``None``.

    The whole record lineage older than the target must still be the selected
    one; the summary content itself is not evidence about its inputs. The
    record path must agree with the *complete* selected ancestry up to the
    turn it actually covers, so a truncated path cannot weaken the proof. The
    proof is scoped to the record's own captured pretarget ancestry (not the
    larger selected ancestry), so a short checkpoint cannot borrow the
    coverage of a completion that finished on turns it never captured.
    A contradiction is ``_INVALID_PROOF``: the record is unusable, not merely
    unprovable.
    """
    if before_turn is None:
        return None
    record_pairs = ancestor_path(record["path"], before_turn)
    if record_pairs is None:
        return _INVALID_PROOF
    if any(selected.get(turn) != branch for turn, branch in record_pairs):
        return _INVALID_PROOF
    canonical = {
        (turn, branch) for turn, branch in selected.items() if turn < before_turn
    }
    if record_pairs:
        last = max(turn for turn, _ in record_pairs)
        if record_pairs != {pair for pair in canonical if pair[0] <= last}:
            return _INVALID_PROOF
    return {
        "events": events,
        "path": record_pairs,
        "watermark": record["watermark"],
        "target": selected_target_event_id(
            events, selected, live_ids, before_turn, canonical
        ),
        "selected": selected,
    }


def _proof_boundary(
    record: dict, selected: dict[int, int], before_turn: int | None
) -> int | None:
    """Turn an unknown-source proof must stop before.

    A targeted restore answers this directly. An ordinary reload has no target:
    the record simply covers the selected turns up to the last one it captured,
    so the first turn it does not cover is the boundary its summaries must
    precede.
    """
    if before_turn is not None:
        return before_turn
    covered = {turn for turn, _ in record["path"]}
    later = [turn for turn in selected if turn not in covered]
    return min(later) if later else None


def _proven_prefix_messages(kept: list[dict], pairs: set[tuple[int, int]]) -> bool:
    """Whether every kept message with coordinates sits on the proven prefix.

    Reused checkpoints must not smuggle sibling-branch or target-turn content
    through an unprovable position; messages without coordinates carry no
    lineage claim of their own.
    """
    for message in kept:
        metadata = message.get("metadata") or {}
        if message["role"] == "system" or metadata.get("compact_summary"):
            continue
        turn, branch = metadata.get("turn_index"), metadata.get("branch_id")
        if type(turn) is int and type(branch) is int and (turn, branch) not in pairs:
            return False
    return True


def _uncovered_calls(
    events: list[dict],
    messages: list[dict],
    live_ids: set[int],
    covered: set[int],
    exemptions: dict[str, set],
    cutoff: int,
    before_turn: int,
) -> list[dict]:
    """Live pre-target calls neither present in the prefix nor proven consumed."""
    call_ids = {
        call.get("id")
        for message in messages
        for call in message.get("tool_calls") or []
    }
    return [
        event
        for event in events
        if event.get("type") == "tool_call"
        and event.get("event_id") in live_ids
        and event.get("event_id") not in covered
        and event.get("event_id") not in exemptions["call_event_ids"]
        and str(event.get("call_id") or event.get("job_id") or "")
        not in exemptions["call_ids"]
        and event.get("event_id", 0) > cutoff
        and event.get("turn_index", before_turn) < before_turn
        and (event.get("tool_call_id") or event.get("call_id")) not in call_ids
    ]


def _present_call_ids(messages: list[dict]) -> set[str]:
    """Call ids the reconstructed prefix already carries."""
    return {
        str(call.get("id"))
        for message in messages
        for call in message.get("tool_calls") or []
        if call.get("id")
    }


def _first_event_id(events: list[dict], selected: dict[int, int], turn: int) -> int:
    """Creation event of the boundary turn, or ``inf`` when it has none."""
    ids = [
        event["event_id"]
        for event in events
        if event.get("event_id") is not None
        and event.get("turn_index") == turn
        and (event.get("turn_index"), event.get("branch_id")) in selected.items()
    ]
    return min(ids) if ids else 1 << 62


def _exemptions() -> dict[str, set]:
    return {"result_ids": set(), "call_ids": set(), "call_event_ids": set()}


def _prefix(
    record: dict,
    selected: dict[int, int],
    before_turn: int | None,
    *,
    proof: dict | None = None,
    empty_boundary: bool = False,
) -> tuple[list[dict], int, frozenset[int], dict[str, set]] | None:
    pairs = {
        (turn, branch)
        for turn, branch in selected.items()
        if before_turn is None or turn < before_turn
    }
    exact = before_turn is None and dict(record["path"]) == selected
    kept, last_turn = [], 0
    proven: set[int] = set()
    exemptions = _exemptions()
    open_turn = None
    for message in record["messages"]:
        role, metadata = message["role"], message.get("metadata") or {}
        if role == "system":
            kept.append(message)
            continue
        if metadata.get("compact_summary") or (
            role == "assistant"
            and isinstance(message["content"], str)
            and message["content"].startswith(
                "[Previous context summary (compact round "
            )
        ):
            inputs = compact_source_path_from_event(metadata)
            if inputs is None and not exact:
                summary_proof = (
                    prove_unknown_summary(message, **proof) if proof else None
                )
                if summary_proof is INVALID_PROOF:
                    return _INVALID_PREFIX
                if summary_proof is None:
                    return None
                proven |= summary_proof.covered_event_ids
                exemptions["result_ids"] |= summary_proof.consumed_result_ids
                exemptions["call_ids"] |= summary_proof.consumed_call_ids
                exemptions["call_event_ids"] |= summary_proof.consumed_call_event_ids
                last_turn = max(last_turn, summary_proof.last_turn)
            elif inputs is not None:
                if not inputs.issubset(pairs):
                    return None
                # A known-source summary may still prove which dispatches it
                # consumed, so a reduced round can borrow that identity. A
                # missing or foreign proof does not invalidate the summary on
                # its own; it only refuses if placement later needs reduction.
                consumed = prove_unknown_summary(message, **proof) if proof else None
                if isinstance(consumed, SummaryProof):
                    exemptions["result_ids"] |= consumed.consumed_result_ids
                    exemptions["call_ids"] |= consumed.consumed_call_ids
                    exemptions["call_event_ids"] |= consumed.consumed_call_event_ids
            if inputs:
                last_turn = max(last_turn, max(turn for turn, _ in inputs))
            kept.append(message)
            continue
        if role == "user" and metadata.get("kind") != "tool_results":
            turn, branch = metadata.get("turn_index"), metadata.get("branch_id")
            if type(turn) is not int or type(branch) is not int:
                if not exact:
                    return None
            elif (turn, branch) not in pairs:
                # Edited target or later turn: dropped from the prefix, so its
                # own unknown origin never taints the retained pretarget part.
                break
            else:
                # Coordinates prove an immutable pretarget position. A batch
                # whose folded job origins are unknown still folds only
                # results issued before this turn, all of which are immutable
                # under an edit of a later turn, so retain it verbatim.
                open_turn = (turn, branch)
                last_turn = max(last_turn, turn)
        elif not exact and open_turn is None:
            return None
        kept.append(message)
    if proven and not _proven_prefix_messages(kept, pairs):
        return None
    if (
        not empty_boundary
        and not any(message["role"] != "system" for message in kept)
        and (
            not exact
            or any(message["role"] != "system" for message in record["messages"])
        )
    ):
        return None
    last = record["path"][-1][0] if exact else last_turn
    return deepcopy(kept), last, frozenset(proven), exemptions


def _boundary_turn(
    record: dict, events: list[dict], selected: dict[int, int], before_turn: int | None
) -> int | None:
    cutoff = record.get("context_cutoff_event_id", 0)
    path = dict(record["path"])
    prior = [
        event
        for event in events
        if event.get("type") == "user_message"
        and event.get("event_id", 0) <= cutoff
        and path.get(event.get("turn_index")) == event.get("branch_id")
    ]
    if (
        not cutoff
        or not prior
        or any(
            selected.get(event["turn_index"]) != event["branch_id"] for event in prior
        )
    ):
        return None
    targets = [
        event
        for event in events
        if event.get("type") == "user_message"
        and selected.get(event.get("turn_index")) == event.get("branch_id")
        and (before_turn is None or event.get("turn_index") == before_turn)
    ]
    if not (before_turn is None and path == selected) and not any(
        event.get("event_id", 0) > cutoff for event in targets
    ):
        return None
    return max(event["turn_index"] for event in prior)


def _replay_context(
    events: list[dict],
    selected: dict[int, int],
    cutoff: int,
    before_turn: int | None,
    replay: Callable,
) -> list[dict]:
    live_ids = select_live_event_ids(events, branch_view=selected)
    ancestor_jobs = {
        str(event.get("call_id") or event.get("job_id") or "")
        for event in events
        if event.get("type") == "tool_call"
        and event.get("event_id") in live_ids
        and (before_turn is None or event.get("turn_index", before_turn) < before_turn)
    }
    retained = [
        event
        for event in events
        if event.get("event_id", 0) > cutoff
        and (
            before_turn is None
            or event.get("turn_index", before_turn) < before_turn
            or event.get("type") == "tool_result"
            and str(event.get("call_id") or event.get("job_id") or "") in ancestor_jobs
        )
    ]
    jobs = {
        str(event.get("call_id") or event.get("job_id") or "")
        for event in retained
        if event.get("type") == "tool_result"
    }
    calls = [
        event
        for event in events
        if event.get("event_id", 0) <= cutoff
        and event.get("type") == "tool_call"
        and str(event.get("call_id") or event.get("job_id") or "") in jobs
    ]
    return replay(
        [*calls, *retained], branch_view=selected, include_metadata=True, resumable=True
    )


def prepare_snapshot(
    store: Any,
    namespace: str,
    events: list[dict],
    selected: dict[int, int],
    *,
    replay: Callable,
    needs_replay: Callable,
    before_turn: int | None = None,
    records: list[dict] | None = None,
    parent_paths: dict[int, tuple[tuple[int, int], ...]] | None = None,
    skip_watermarks: set[int] | None = None,
) -> dict | None:
    """Restore a checkpoint prefix; supplied paths match unchanged raw events and order."""
    if records is None:
        records = load_records(store, namespace)
    if not records:
        return None
    raw_pairs = {
        (event.get("turn_index"), event.get("branch_id"))
        for event in events
        if event.get("type") == "user_message"
    }
    live_ids = select_live_event_ids(
        events, branch_view=selected, parent_paths=parent_paths
    )
    base_covered = compact_covered_event_ids(
        events, selected, parent_paths=parent_paths
    )
    candidates, repairs = [], []
    for record in records:
        if skip_watermarks and record["watermark"] in skip_watermarks:
            continue
        if not set(map(tuple, record["path"])).issubset(raw_pairs) or not _fresh(
            store, namespace, events, record["watermark"]
        ):
            continue
        cutoff = record.get("context_cutoff_event_id", 0)
        boundary_turn = _boundary_turn(record, events, selected, before_turn)
        proof = _proof_inputs(
            record,
            selected,
            events,
            live_ids,
            _proof_boundary(record, selected, before_turn),
        )
        if proof is _INVALID_PROOF:
            continue
        if cutoff:
            if boundary_turn is None:
                continue
            repairs.append((boundary_turn, record["watermark"], cutoff, record))
        prefix = _prefix(
            record,
            selected,
            before_turn,
            proof=proof,
            empty_boundary=boundary_turn is not None,
        )
        if prefix is _INVALID_PREFIX:
            # Drop the boundary repair registered above: a contradicting
            # record must not be silently rebuilt from its raw cutoff.
            repairs[:] = [repair for repair in repairs if repair[3] is not record]
            continue
        if prefix is None:
            continue
        messages, last_turn, proven, exemptions = prefix
        covered = base_covered | proven
        last_turn = max(last_turn, boundary_turn or 0)
        whole = before_turn is None and record["path"] == sorted(
            [t, b] for t, b in selected.items()
        )
        if whole and not any(
            message["role"] != "system" for message in record["messages"]
        ):
            cutoff = max(cutoff, record["watermark"])
        if cutoff:
            repairs.append((last_turn, record["watermark"], cutoff, record))
        if whole:
            tail = [
                event
                for event in events
                if event.get("event_id", 0) > record["watermark"]
                and event.get("event_id") in live_ids
            ]
        else:
            tail = [
                event
                for event in events
                if event.get("event_id") in live_ids
                and type(event.get("turn_index")) is int
                and event["turn_index"] > last_turn
                and (before_turn is None or event["turn_index"] < before_turn)
            ]
        if any(
            event.get("event_id", 0) > record["watermark"]
            and event.get("event_id") in live_ids
            and event.get("type")
            in {
                "text_chunk",
                "text",
                "assistant_reasoning",
                "assistant_tool_calls",
                "assistant_message",
                "assistant_image",
                "user_message",
                "tool_call",
                "compact_complete",
                "compact_replace",
                "context_cleared",
            }
            and event.get("turn_index", last_turn) <= last_turn
            for event in events
        ):
            continue
        if any(
            event.get("type")
            in {
                "compact_complete",
                "compact_replace",
                "context_cleared",
                "system_prompt_set",
            }
            for event in tail
        ):
            continue
        pending = {
            message.get("metadata", {}).get("job_id")
            for message in messages
            if message.get("metadata", {}).get("tool_result_phase") == "pending"
        }
        if any(
            event.get("type") == "tool_result"
            and event.get("event_id") not in covered
            and (event.get("call_id") or event.get("job_id")) in pending
            for event in events
        ):
            continue
        messages.extend(
            replay(tail, branch_view=selected, include_metadata=True, resumable=True)
        )
        # Every live pre-target call the snapshot does not otherwise account
        # for must be present. A call may only be omitted when an explicitly
        # consumed result proves it. Exempting calls merely because they
        # predate the record watermark drops a tool call the replayed prompt
        # still needs, so "settled before the snapshot" is not a proof.
        if before_turn is not None:
            gaps = _uncovered_calls(
                events, messages, live_ids, covered, exemptions, cutoff, before_turn
            )
            if gaps:
                target_event_id = _first_event_id(events, selected, before_turn)
                repaired = repair_gap_messages(
                    gaps,
                    events,
                    target_event_id=target_event_id,
                    present_call_ids=_present_call_ids(messages),
                    represented_event_ids=covered,
                    cutoff=cutoff,
                    eligible_ids=live_ids,
                )
                if repaired is None:
                    continue
                placed = place_repaired_messages(
                    messages,
                    repaired,
                    events,
                    eligible_ids=live_ids,
                    consumed_call_event_ids=exemptions["call_event_ids"],
                )
                if placed is None:
                    continue
                messages = placed
                if _uncovered_calls(
                    events,
                    messages,
                    live_ids,
                    covered,
                    exemptions,
                    cutoff,
                    before_turn,
                ):
                    continue
        # Keep calls for ownership: a late result that the proof does not cover
        # must remain visible to ``needs_replay`` instead of losing its call and
        # disappearing from the dependency check. Only explicitly consumed
        # results are exempt; results inside the checkpoint cutoff are already
        # represented by the checkpoint itself.
        checked_events = [
            event
            for event in events
            if not (
                event.get("type") == "tool_result"
                and event.get("event_id") in exemptions["result_ids"]
            )
            and (
                event.get("type") != "tool_result" or event.get("event_id", 0) > cutoff
            )
        ]
        if needs_replay(checked_events, selected, snapshot=messages):
            continue
        candidates.append(
            {
                "messages": messages,
                "watermark": record["watermark"],
                "compact_state": record["compact_state"],
                "context_cutoff_event_id": cutoff,
                "selected": selected,
                "_rank": (last_turn, record["watermark"]),
            }
        )
    if not candidates:
        if not repairs:
            return None
        _, _, cutoff, record = max(repairs, key=lambda repair: repair[:2])
        messages = [
            deepcopy(message)
            for message in record["messages"]
            if message["role"] == "system"
        ]
        messages.extend(_replay_context(events, selected, cutoff, before_turn, replay))
        return {
            "messages": messages,
            "compact_state": replayed_compact_state(messages),
            "context_cutoff_event_id": cutoff,
            "selected": selected,
        }
    best = max(candidates, key=lambda candidate: candidate["_rank"])
    best.pop("_rank")
    return best
