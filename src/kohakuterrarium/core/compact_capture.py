"""Actual-capture compaction coverage.

``compute_replaced_range`` derives a summary's replacement span from the
configured live-turn count, which only holds while the live zone is a suffix
of whole user turns. These helpers instead report a span only when every event
it would swallow on the selected path is proven to belong to this capture: a
fully captured turn, an event id carried by a captured message, or coverage
inherited from the compact_complete of a consumed summary. Otherwise they
return ``None`` rather than claim false coverage.

Message identities themselves are resolved by
:mod:`kohakuterrarium.core.compact_binding`; this module keeps the summary
lineage helpers, merges inherited coverage, and validates the final span.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from kohakuterrarium.core.compact_binding import (
    _event_id,
    _is_summary,
    _metadata,
    _on_path,
    _pair,
    _restated_input,
    _summary_body,
    resolve_capture_ids,
    uses_canonical_rounds as uses_canonical_rounds,
)
from kohakuterrarium.core.compact_splice import is_real_user_message
from kohakuterrarium.llm.message import ASSISTANT_STATE_KEYS

# Events that carry no conversation content, so a span may pass over them.
_OPERATIONAL_TYPES = frozenset(
    {
        "background_result",
        "scratchpad_write",
        "token_usage",
        "turn_token_usage",
        "cache_stats",
        "compact_skipped",
        "processing_complete",
        "compact_decision",
        "compact_start",
        "plugin_hook_timing",
        "processing_end",
        "processing_start",
    }
)


def message_pairs(messages: Sequence[Any]) -> set[tuple[int, int]]:
    """Turn/branch pairs a message list explicitly identifies."""
    pairs: set[tuple[int, int]] = set()
    for msg in messages:
        pair = _pair(_metadata(msg))
        if pair is not None:
            pairs.add(pair)
    return pairs


def open_user_pair(messages: Sequence[Any]) -> tuple[int, int] | None:
    """The pair of the last real user message that opened a turn."""
    opened: tuple[int, int] | None = None
    for msg in messages:
        if not is_real_user_message(msg):
            continue
        pair = _pair(_metadata(msg))
        if pair is not None:
            opened = pair
    return opened


def _coerce_pairs(raw: Any) -> set[tuple[int, int]] | None:
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    pairs: set[tuple[int, int]] = set()
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        if any(type(value) is not int or value <= 0 for value in item):
            return None
        pairs.add((item[0], item[1]))
    return pairs


def _result_ids(metadata: Mapping[str, Any]) -> set[int]:
    raw = metadata.get("compact_result_event_ids")
    if not isinstance(raw, (list, tuple)):
        return set()
    return {value for value in raw if type(value) is int and value > 0}


def known_captured_event_ids(messages: Sequence[Any]) -> set[int]:
    """Event ids a captured message explicitly names as consumed."""
    ids: set[int] = set()
    for msg in messages:
        metadata = _metadata(msg)
        event_id = metadata.get("result_event_id")
        if type(event_id) is int and event_id > 0:
            ids.add(event_id)
        ids |= _result_ids(metadata)
    return ids


def matching_compact_complete(
    summary_message: Any, events: Sequence[dict]
) -> dict | None:
    """The persisted compact_complete matching one consumed summary message.

    Matching is exact (round *and* body); duplicate copies count only while
    they agree on coverage, inputs and result ids.
    """
    parsed = _summary_body(summary_message)
    if parsed is None:
        return None
    round_no, body = parsed
    candidates: list[dict] = []
    for event in events:
        if event.get("type") != "compact_complete":
            continue
        summary = event.get("summary") or event.get("summary_text") or ""
        if summary != body:
            continue
        event_round = event.get("round")
        if type(event_round) is int and event_round != round_no:
            continue
        if not isinstance(event.get("replaced_from_event_id"), int) or not isinstance(
            event.get("replaced_to_event_id"), int
        ):
            continue
        candidates.append(event)
    if not candidates:
        return None
    signatures = {
        (
            event.get("replaced_from_event_id"),
            event.get("replaced_to_event_id"),
            tuple(sorted(_result_ids(event))),
            tuple(sorted(_coerce_pairs(event.get("compact_path")) or ())),
            (
                None
                if "compact_source_path" not in event
                else (
                    "null"
                    if _coerce_pairs(event.get("compact_source_path")) is None
                    else tuple(
                        sorted(_coerce_pairs(event.get("compact_source_path")) or ())
                    )
                )
            ),
        )
        for event in candidates
    }
    return candidates[-1] if len(signatures) == 1 else None


def _inherited_coverage(
    captured_messages: Sequence[Any],
    events: Sequence[dict],
    path: set[tuple[int, int]],
    *,
    strict: bool = False,
) -> tuple[set[int], int, int] | None:
    """Coverage of consumed summaries, inherited from their own completion.

    Only a summary whose recorded lineage is a subset of the current path can be
    inherited; ``strict`` mode refuses the span instead of skipping such a summary.
    """
    cover_ids: set[int] = set()
    low: int | None = None
    high: int | None = None
    for msg in captured_messages:
        metadata = _metadata(msg)
        is_summary = metadata.get("compact_summary") or _summary_body(msg) is not None
        if not is_summary:
            continue
        event = matching_compact_complete(msg, events)
        if event is None:
            if strict:
                return None
            continue
        old_path = _coerce_pairs(event.get("compact_path"))
        if old_path is None or not old_path <= path:
            if strict:
                return None
            continue
        if "compact_source_path" in event:
            source = _coerce_pairs(event.get("compact_source_path"))
            if source is None or not (source | old_path) <= path:
                if strict:
                    return None
                continue
        start = event["replaced_from_event_id"]
        end = event["replaced_to_event_id"]
        ids = {
            evt["event_id"]
            for evt in events
            if type(evt.get("event_id")) is int
            and start <= evt["event_id"] <= end
            and (
                evt.get("turn_index"),
                evt.get("branch_id"),
            )
            in old_path
        }
        ids |= _result_ids(event)
        if not ids:
            if strict:
                return None
            continue
        cover_ids |= ids
        low = min(start, low) if low is not None else start
        high = max(end, high) if high is not None else end
    if low is None or high is None:
        return None
    return cover_ids, low, high


def compute_replaced_range(
    events: list[dict],
    keep_count: int,
    path: set[tuple[int, int]],
) -> tuple[int, int] | None:
    """Legacy event id range: the last ``keep_count`` path user turns stay live.

    Only used when the captured messages carry no turn provenance at all.
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


def _partial_pairs(
    captured_messages: Sequence[Any],
    captured_pairs: set[tuple[int, int]],
    retained_messages: Sequence[Any],
    retain_latest_user: bool,
) -> set[tuple[int, int]]:
    partial = message_pairs(retained_messages)
    opened = open_user_pair(captured_messages)
    if opened is not None:
        if retain_latest_user:
            partial.add(opened)
        elif retained_messages and not is_real_user_message(retained_messages[0]):
            # The live zone starts mid-turn: that open turn stays unproven.
            partial.add(opened)
    return partial & captured_pairs


def compute_captured_range(
    events: list[dict],
    path: set[tuple[int, int]],
    captured_messages: Sequence[Any] = (),
    retained_messages: Sequence[Any] = (),
    *,
    retain_latest_user: bool = False,
    keep_count: int | None = None,
) -> tuple[int, int] | None:
    """Replacement span matching the messages this compaction consumed.

    Canonical sessions are bound exactly by
    :func:`compute_exact_captured_range`; otherwise whole captured turns define
    the outer span, a turn split by the live boundary contributes only ids
    carried by a captured message plus inherited summary coverage, and any
    unproven path event inside the span aborts it (a too-wide range would
    silently drop raw history from replay).
    """
    if not path:
        return None
    if uses_canonical_rounds(events):
        return compute_exact_captured_range(
            events,
            path,
            captured_messages,
            retained_messages,
            retain_latest_user=retain_latest_user,
        )
    captured_pairs = message_pairs(captured_messages)
    if not captured_pairs:
        if keep_count is None:
            return None
        return compute_replaced_range(events, keep_count, path)
    partial = _partial_pairs(
        captured_messages, captured_pairs, retained_messages, retain_latest_user
    )
    complete = captured_pairs - partial
    known = known_captured_event_ids(captured_messages)
    inherited = _inherited_coverage(captured_messages, events, path)
    inherited_ids = inherited[0] if inherited is not None else set()
    opened = open_user_pair(captured_messages)
    retained_pairs = message_pairs(retained_messages)
    opened_retained = opened is not None and opened in retained_pairs

    base: set[int] = set()
    for event in events:
        eid = event.get("event_id")
        pair = (event.get("turn_index"), event.get("branch_id"))
        if type(eid) is not int or pair not in path:
            continue
        if pair in complete or eid in known or eid in inherited_ids:
            base.add(eid)
    if not base:
        return None
    low, high = min(base), max(base)
    if inherited is not None:
        low, high = min(low, inherited[1]), max(high, inherited[2])

    for event in events:
        eid = event.get("event_id")
        pair = (event.get("turn_index"), event.get("branch_id"))
        if type(eid) is not int or pair not in path or not low <= eid <= high:
            continue
        if (
            retain_latest_user
            and pair == opened
            and event.get("type") == "user_message"
        ):
            # The reattached user message itself stays live even when an
            # inherited span would otherwise cover it.
            return None
        if pair in complete or eid in known or eid in inherited_ids:
            continue
        if pair == opened and not opened_retained:
            # Open-turn prefix captured before the boundary.
            continue
        return None
    return low, high


def _restated_reasoning(
    event: dict, preceding: Sequence[dict], proven: set[int]
) -> bool:
    state = {
        key: event[key]
        for key in ASSISTANT_STATE_KEYS
        if event.get(key) not in (None, "", [], {})
    }
    if not state:
        return False
    for previous in reversed(preceding):
        if previous.get("type") in {"user_message", "assistant_round_start"}:
            return False
        if previous.get("type") == "assistant_message":
            payload = previous.get("message") or {}
            return (
                previous.get("event_id") in proven
                and _pair(previous) == _pair(event)
                and previous.get("parent_branch_path")
                == event.get("parent_branch_path")
                and all(payload.get(key) == value for key, value in state.items())
            )
    return False


def compute_exact_captured_range(
    events: list[dict],
    path: set[tuple[int, int]],
    captured_messages: Sequence[Any] = (),
    retained_messages: Sequence[Any] = (),
    *,
    retain_latest_user: bool = False,
) -> tuple[int, int] | None:
    """Replacement span proven message-by-message against persisted records.

    Message identities are resolved by
    :func:`kohakuterrarium.core.compact_binding.resolve_capture_ids`; consumed
    summaries then contribute inherited coverage, and every remaining path
    event inside the resulting span must be one of those proven ids, an
    operational record, or a user-input echo of a proven user message.
    """
    if not path:
        return None
    resolved = resolve_capture_ids(
        events,
        path,
        captured_messages,
        retained_messages,
        retain_latest_user=retain_latest_user,
    )
    if resolved is None:
        return None
    proven, excluded = resolved
    for position, event in enumerate(events):
        if (
            event.get("type") == "assistant_reasoning"
            and (event_id := _event_id(event)) is not None
            and _on_path(event, path)
            and _restated_reasoning(event, events[:position], proven)
        ):
            proven.add(event_id)
    summaries = any(_is_summary(message) for message in captured_messages)
    inherited = _inherited_coverage(captured_messages, events, path, strict=True)
    if inherited is None and summaries:
        # A captured summary that cannot prove its own lineage is not coverage.
        return None
    if not proven and inherited is None:
        return None
    low = min(proven) if proven else inherited[1]
    high = max(proven) if proven else inherited[2]
    if inherited is not None:
        low, high = min(low, inherited[1]), max(high, inherited[2])
        proven |= inherited[0]
    if any(low <= event_id <= high for event_id in excluded):
        return None
    index: dict[int, dict] = {
        event_id: event
        for event in events
        if (event_id := _event_id(event)) is not None
    }
    completions = {
        matched["event_id"]
        for message in captured_messages
        if _is_summary(message)
        and (matched := matching_compact_complete(message, events)) is not None
    }
    for position, event in enumerate(events):
        event_id = _event_id(event)
        if (
            event_id is None
            or not _on_path(event, path)
            or not low <= event_id <= high
            or event_id in proven
        ):
            continue
        if event_id in completions or event.get("type") in _OPERATIONAL_TYPES:
            continue
        if event.get("type") == "user_input" and _restated_input(event, index, proven):
            continue
        return None
    return low, high
