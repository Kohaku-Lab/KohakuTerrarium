"""Actual-capture compaction coverage.

``compute_replaced_range`` derives a summary's replacement span from the
configured live-turn count. That is only correct while the live zone is a
suffix of whole user turns. Token-tightened boundaries and long tool rounds
can split a turn, folded inputs can move results away from their issuing
turn, and then the persisted span no longer matches the messages that were
actually summarized.

These helpers read the captured and retained messages and report a span only
when every event it would swallow on the selected path is proven to belong
to this capture: a fully captured turn, an event id carried by a captured
message, or coverage inherited from the compact_complete event whose summary
was consumed. Otherwise they return ``None`` instead of claiming false
coverage.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from kohakuterrarium.core.compact_splice import is_real_user_message

_SUMMARY_PREFIX = "[Previous context summary (compact round "


def _metadata(msg: Any) -> Mapping[str, Any]:
    metadata = getattr(msg, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _pair(metadata: Mapping[str, Any]) -> tuple[int, int] | None:
    turn = metadata.get("turn_index")
    branch = metadata.get("branch_id")
    if type(turn) is not int or type(branch) is not int:
        return None
    if turn <= 0 or branch <= 0:
        return None
    return turn, branch


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


def _summary_body(msg: Any) -> tuple[int, str] | None:
    content = getattr(msg, "content", None)
    if not isinstance(content, str) or not content.startswith(_SUMMARY_PREFIX):
        return None
    head, sep, body = content[len(_SUMMARY_PREFIX) :].partition(")]")
    if not sep:
        return None
    try:
        round_no = int(head)
    except ValueError:
        return None
    if body.startswith("\n\n"):
        body = body[2:]
    else:
        body = body.lstrip("\n")
    return round_no, body


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

    Matching is exact (round *and* summary body). Duplicate copies are
    tolerated only while they agree on coverage, inputs and result ids; an
    ambiguous match proves nothing.
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
) -> tuple[set[int], int, int] | None:
    """Coverage of consumed summaries, inherited from their own completion.

    Only a summary whose recorded lineage is a subset of the current path can
    be inherited: a pathless (legacy) or unprovably sourced completion never
    borrows the caller's coverage.
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
            continue
        old_path = _coerce_pairs(event.get("compact_path"))
        if old_path is None or not old_path <= path:
            continue
        if "compact_source_path" in event:
            source = _coerce_pairs(event.get("compact_source_path"))
            if source is None or not (source | old_path) <= path:
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
    """Event id range a compaction covers, computed from the event log.

    Legacy fallback: only events on the agent's current branch ``path`` are
    considered; the live zone keeps the last ``keep_count`` user turns of
    that path, so everything older is replaced by the summary. Used when the
    captured messages carry no turn provenance at all.
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
            # The live zone starts mid-turn: the open user turn continues
            # past the boundary and cannot be replaced as a whole.
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

    Whole captured turns define the outer span. A turn split by the live
    boundary contributes only events whose ids are explicitly carried by a
    captured message, plus anything inherited from a consumed summary. Any
    path event in the resulting span that is not proven to belong to the
    capture aborts the whole span: a too-wide range would silently drop raw
    history from replay.
    """
    if not path:
        return None
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
