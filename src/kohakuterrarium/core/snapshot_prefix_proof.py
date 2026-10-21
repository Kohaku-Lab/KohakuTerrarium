"""Prove a persisted checkpoint summary whose source path stayed unknown.

A stored summary without a recorded source path cannot name the turns it was
written from. It is reusable for a targeted edit only when the event log still
holds exactly one completion on the checkpoint's own lineage that finished
before the edited user event was created. Nothing here guesses a source: the
proof is the persisted completion identity (round, body, compact path) plus
the event-ordering bounds. Consumed result identities are validated against
the log rather than trusted, so a stale or future identifier cannot widen the
coverage a summary claims.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from kohakuterrarium.core.compact_branch import (
    compact_path_from_event,
    compact_source_path_from_event,
)

_MARKER = re.compile(r"\A\[Previous context summary \(compact round (\d+)\)\]\n*")
_COMPLETIONS = ("compact_complete", "compact_replace")

# Returned instead of ``None`` when a completion matches a summary's identity
# but contradicts the proven lineage. Callers must drop the record rather than
# treat it as an ordinary cache miss.
INVALID = object()


@dataclass(frozen=True)
class SummaryProof:
    """What one proven completion lets a checkpoint summary stand for."""

    covered_event_ids: frozenset[int]
    last_turn: int
    consumed_result_ids: frozenset[int] = field(default_factory=frozenset)
    consumed_call_ids: frozenset[str] = field(default_factory=frozenset)
    consumed_call_event_ids: frozenset[int] = field(default_factory=frozenset)


def ancestor_path(
    record_path: Sequence[Any], before_turn: int
) -> set[tuple[int, int]] | None:
    """Checkpoint lineage coordinates older than the edit target."""
    path: set[tuple[int, int]] = set()
    for item in record_path:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        turn, branch = item
        if type(turn) is not int or type(branch) is not int:
            return None
        if turn < before_turn:
            path.add((turn, branch))
    return path


def selected_target_event_id(
    events: Sequence[dict],
    selected: Mapping[int, int],
    live_ids: set[int],
    before_turn: int | None,
    path: set[tuple[int, int]],
) -> int | None:
    """The one live original user event an edit of ``before_turn`` replaces."""
    if before_turn is None:
        return None
    branch = selected.get(before_turn)
    found: list[int] = []
    for event in events:
        if (
            event.get("type") != "user_message"
            or event.get("turn_index") != before_turn
            or event.get("event_id") not in live_ids
        ):
            continue
        if type(branch) is int:
            if event.get("branch_id") != branch:
                continue
        elif not _on_path(event, path):
            continue
        found.append(event["event_id"])
    return found[0] if len(found) == 1 else None


def summary_identity(message: Mapping[str, Any]) -> tuple[int, str] | None:
    """Round number and body text a stored summary message shows."""
    content = message.get("content")
    if not isinstance(content, str):
        return None
    match = _MARKER.match(content)
    if match is None:
        return None
    body = content[match.end() :]
    return (int(match.group(1)), body) if body.strip() else None


def prove_unknown_summary(
    message: Mapping[str, Any],
    *,
    events: Sequence[dict],
    path: set[tuple[int, int]],
    watermark: int,
    target: int | None,
    selected: Mapping[int, int] | None = None,
) -> SummaryProof | None | object:
    """Coverage proven for one unknown-source summary, or ``None``/``INVALID``.

    ``INVALID`` means a completion claimed the summary's identity but its
    coordinates, parents or consumed identities contradict the proven
    lineage; such a record may not be repaired from raw events later.
    """
    identity = summary_identity(message)
    if identity is None or target is None:
        return None
    matches = [
        event
        for event in events
        if _completion_matches(event, identity, watermark, target)
    ]
    authoritative: list[Mapping[str, Any]] = []
    for event in matches:
        compact_path = compact_path_from_event(event)
        if not compact_path or not compact_path <= path:
            continue
        # A recorded input that is not on the checkpoint lineage would let the
        # summary depend on content the edit can change.
        source = compact_source_path_from_event(event)
        if source is not None and not source <= path:
            continue
        # The completion event itself must sit on the same selected ancestry:
        # a matching body alone does not prove which branch produced it.
        if not _completion_lineage_ok(event, path, selected):
            return INVALID
        authoritative.append(event)
    if len(authoritative) != 1:
        return None
    completion = authoritative[0]
    start = completion.get("replaced_from_event_id")
    stop = completion.get("replaced_to_event_id")
    if (
        type(start) is not int
        or type(stop) is not int
        or not 0 < start <= stop < completion["event_id"]
    ):
        return INVALID
    consumed = _consumed_identities(events, completion, path)
    if consumed is None:
        return INVALID
    result_ids, call_ids, call_event_ids = consumed
    return SummaryProof(
        covered_event_ids=_covered_ids(events, completion, path),
        last_turn=_completion_turn(completion),
        consumed_result_ids=result_ids,
        consumed_call_ids=call_ids,
        consumed_call_event_ids=call_event_ids,
    )


def _completion_matches(
    event: Any,
    identity: tuple[int, str],
    watermark: int,
    target: int,
) -> bool:
    """Whether one event claims the identity of the summary being proven."""
    if not isinstance(event, Mapping) or event.get("type") not in _COMPLETIONS:
        return False
    event_id = event.get("event_id")
    if type(event_id) is not int or event_id > watermark or event_id >= target:
        return False
    completion = _completion_identity(event)
    if completion is None:
        return False
    if completion[0] != identity[0]:
        return False
    return completion[1].strip() == identity[1].strip()


def _completion_lineage_ok(
    event: Mapping[str, Any],
    path: set[tuple[int, int]],
    selected: Mapping[int, int] | None,
) -> bool:
    """Completion coordinates and parents must lie on the proven lineage."""
    turn, branch = event.get("turn_index"), event.get("branch_id")
    if type(turn) is int and type(branch) is int:
        if (turn, branch) not in path:
            return False
    parents = _parent_pairs(event.get("parent_branch_path"))
    if parents is None:
        return False
    for turn, branch in parents:
        if (turn, branch) not in path:
            return False
        if selected is not None and selected.get(turn) != branch:
            return False
    return True


def _parent_pairs(raw: Any) -> set[tuple[int, int]] | None:
    """Parent lineage of one event, or ``None`` when explicitly malformed."""
    if raw is None:
        return set()
    if not isinstance(raw, (list, tuple)):
        return None
    pairs: set[tuple[int, int]] = set()
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        turn, branch = item
        if type(turn) is not int or type(branch) is not int:
            return None
        pairs.add((turn, branch))
    return pairs


def _completion_identity(event: Mapping[str, Any]) -> tuple[int, str] | None:
    """Round number and body text a persisted completion carries."""
    round_number = event.get("round")
    if type(round_number) is not int or round_number <= 0:
        return None
    raw = event.get("summary") or event.get("summary_text") or ""
    if not isinstance(raw, str):
        return None
    match = _MARKER.match(raw)
    return round_number, (raw[match.end() :] if match else raw)


def _completion_turn(event: Mapping[str, Any]) -> int:
    """Newest turn the proven summary replaced."""
    turns = {turn for turn, _ in compact_path_from_event(event) or ()}
    turn = event.get("turn_index")
    if type(turn) is int:
        turns.add(turn)
    return max(turns, default=0)


def _consumed_identities(
    events: Sequence[dict],
    completion: Mapping[str, Any],
    path: set[tuple[int, int]],
) -> tuple[frozenset[int], frozenset[str], frozenset[int]] | None:
    """Validate the completion's explicitly consumed results against the log.

    Every named identity must be a real ``tool_result`` on the proven lineage
    that strictly precedes the completion, and it must have exactly one
    producing ``tool_call`` on that same lineage which itself precedes the
    result. Anything else (future, non-result, sibling-owned, duplicated or
    unknown) makes the proof unusable rather than silently widening coverage.
    """
    raw = completion.get("compact_result_event_ids")
    raw_ids: set[int] = set()
    if raw is not None:
        if not isinstance(raw, (list, tuple)):
            return None
        for value in raw:
            if type(value) is not int or value <= 0:
                return None
            raw_ids.add(value)
    if not raw_ids:
        return frozenset(), frozenset(), frozenset()
    completion_id = completion.get("event_id")
    if type(completion_id) is not int:
        return None
    by_id: dict[int, dict] = {}
    for event in events:
        event_id = event.get("event_id")
        if type(event_id) is int:
            by_id.setdefault(event_id, event)
    result_ids: set[int] = set()
    call_ids: set[str] = set()
    call_event_ids: set[int] = set()
    for result_id in raw_ids:
        # A consumed result must strictly precede the completion that names it.
        if result_id >= completion_id:
            return None
        result = by_id.get(result_id)
        if result is None or result.get("type") != "tool_result":
            return None
        if not _on_path(result, path) or not _completion_lineage_ok(result, path, None):
            return None
        identity = _call_identity(result)
        if not identity:
            return None
        owners = [
            event
            for event in events
            if event.get("type") == "tool_call"
            and _call_identity(event) == identity
            and _on_path(event, path)
        ]
        # Ownership must resolve to exactly one producing call on the proven
        # lineage; a duplicated identity would let one result mask another.
        if len(owners) != 1 or not _completion_lineage_ok(owners[0], path, None):
            return None
        owner_id = owners[0].get("event_id")
        # The producing call itself must precede the result it produced.
        if type(owner_id) is not int or owner_id >= result_id:
            return None
        result_ids.add(result_id)
        call_ids.add(identity)
        call_event_ids.add(owner_id)
    return frozenset(result_ids), frozenset(call_ids), frozenset(call_event_ids)


def _call_identity(event: Mapping[str, Any]) -> str:
    return str(event.get("call_id") or event.get("job_id") or "")


def _covered_ids(
    events: Sequence[dict], completion: Mapping[str, Any], path: set[tuple[int, int]]
) -> frozenset[int]:
    """Event identities the summary replaces, limited to its own lineage."""
    covered = set(_result_ids(completion))
    start = completion.get("replaced_from_event_id")
    stop = completion.get("replaced_to_event_id")
    if type(start) is not int or type(stop) is not int or start > stop:
        return frozenset(covered)
    for event in events:
        event_id = event.get("event_id")
        if type(event_id) is not int or not start <= event_id <= stop:
            continue
        if not _on_path(event, path):
            continue
        covered.add(event_id)
    return frozenset(covered)


def _on_path(event: Mapping[str, Any], path: set[tuple[int, int]]) -> bool:
    """Whether an event's coordinates sit on the proven lineage."""
    turn, branch = event.get("turn_index"), event.get("branch_id")
    if type(turn) is not int or type(branch) is not int:
        return True
    return (turn, branch) in path


def _result_ids(event: Mapping[str, Any]) -> set[int]:
    """Explicitly consumed result identities of a completion."""
    raw = event.get("compact_result_event_ids")
    if not isinstance(raw, (list, tuple)):
        return set()
    return {value for value in raw if type(value) is int and value > 0}
