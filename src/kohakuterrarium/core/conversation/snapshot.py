"""Select compatible conversation checkpoints and reconstruct their live suffix."""

import re
from copy import deepcopy
from typing import Any, Callable

from kohakuterrarium.core.compact.branch import compact_source_path_from_event
from kohakuterrarium.session.history import (
    compact_covered_event_ids,
    select_live_event_ids,
)
from kohakuterrarium.session.snapshot import load_records


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
    # This namespace ends below the watermark, so another agent reached it
    # exactly when the store-wide maximum did.
    store_max = getattr(store, "global_max_event_id", None)
    if type(store_max) is int:
        return store_max >= watermark
    getter = getattr(store, "get_all_events", None)
    if not callable(getter):
        return False
    return any(
        not key.startswith(f"{namespace}:e") and event.get("event_id", 0) >= watermark
        for key, event in getter()
    )


def _prefix(
    record: dict,
    selected: dict[int, int],
    before_turn: int | None,
    *,
    empty_boundary: bool = False,
) -> tuple[list[dict], int] | None:
    pairs = {
        (turn, branch)
        for turn, branch in selected.items()
        if before_turn is None or turn < before_turn
    }
    exact = before_turn is None and dict(record["path"]) == selected
    kept, last_turn = [], 0
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
            if (
                inputs is None
                and not exact
                or inputs is not None
                and not inputs.issubset(pairs)
            ):
                return None
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
                break
            else:
                open_turn = (turn, branch)
                last_turn = max(last_turn, turn)
        elif not exact and open_turn is None:
            return None
        kept.append(message)
    if (
        not empty_boundary
        and not any(message["role"] != "system" for message in kept)
        and (
            not exact
            or any(message["role"] != "system" for message in record["messages"])
        )
    ):
        return None
    return deepcopy(kept), record["path"][-1][0] if exact else last_turn


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
) -> dict | None:
    """Restore a checkpoint prefix; supplied paths match unchanged raw events and order."""
    if records is None:
        records = load_records(store, namespace)
    if not records:
        return None
    # Every recorded turn counts: trigger- and background-opened turns have no
    # user_message yet still sit on a checkpoint's branch path.
    raw_pairs = {
        (event.get("turn_index"), event.get("branch_id"))
        for event in events
        if type(event.get("turn_index")) is int and type(event.get("branch_id")) is int
    }
    live_ids = select_live_event_ids(
        events, branch_view=selected, parent_paths=parent_paths
    )
    covered = compact_covered_event_ids(events, selected, parent_paths=parent_paths)
    # Checkpoint paths record only real turns; pre-turn events sit at (0, 0).
    path_view = {t: b for t, b in selected.items() if t > 0 and b > 0}
    candidates, repairs = [], []
    for record in records:
        if not set(map(tuple, record["path"])).issubset(raw_pairs) or not _fresh(
            store, namespace, events, record["watermark"]
        ):
            continue
        cutoff = record.get("context_cutoff_event_id", 0)
        boundary_turn = _boundary_turn(record, events, path_view, before_turn)
        if cutoff:
            if boundary_turn is None:
                continue
            repairs.append((boundary_turn, record["watermark"], cutoff, record))
        prefix = _prefix(
            record, path_view, before_turn, empty_boundary=boundary_turn is not None
        )
        if prefix is None:
            continue
        messages, last_turn = prefix
        last_turn = max(last_turn, boundary_turn or 0)
        whole = before_turn is None and record["path"] == sorted(
            [t, b] for t, b in path_view.items()
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
        if before_turn is not None:
            call_ids = {
                call.get("id")
                for message in messages
                for call in message.get("tool_calls") or []
            }
            if any(
                event.get("type") == "tool_call"
                and event.get("event_id") in live_ids
                and event.get("event_id") not in covered
                and event.get("event_id", 0) > cutoff
                and event.get("turn_index", before_turn) < before_turn
                and (event.get("tool_call_id") or event.get("call_id")) not in call_ids
                for event in events
            ):
                continue
        checked_events = [
            event
            for event in events
            if event.get("type") != "tool_result" or event.get("event_id", 0) > cutoff
        ]
        if needs_replay(checked_events, selected, snapshot=messages):
            continue
        candidates.append(
            {
                "messages": messages,
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
