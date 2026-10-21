"""Project canonical assistant rounds onto replay event streams.

Replay consumers need a complete OpenAI-shaped ``assistant_message`` at the
round's original start so later tool results remain ordered after the
announcement. Streamed display pieces stay in place as
``assistant_round_display`` so compact/monotonic identity is preserved.
"""

import json
from copy import deepcopy
from typing import Any

from kohakuterrarium.llm.antigravity_format import replay_target
from kohakuterrarium.llm.message import ASSISTANT_STATE_KEYS, ImagePart

_DISPLAY_TYPES = frozenset(
    {
        "text_chunk",
        "text",
        "assistant_text",
        "assistant_image",
        "assistant_reasoning",
        "assistant_tool_calls",
    }
)
_BOUNDARY_TYPES = frozenset({"assistant_round_start", "user_message"})


def _positive_id(value: Any) -> int | None:
    """Return a strictly positive int id, rejecting bools and non-ints."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _lineage(event: dict[str, Any]) -> tuple[Any, Any, tuple[tuple[int, int], ...]]:
    raw_path = event.get("parent_branch_path") or ()
    path: list[tuple[int, int]] = []
    try:
        for item in raw_path:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                turn, branch = item
                if isinstance(turn, int) and isinstance(branch, int):
                    path.append((turn, branch))
    except TypeError:
        path = []
    return (event.get("turn_index"), event.get("branch_id"), tuple(path))


def _conflicting_lineage(
    left: tuple[Any, Any, tuple[tuple[int, int], ...]],
    right: tuple[Any, Any, tuple[tuple[int, int], ...]],
) -> bool:
    """Compare shared, known turn/branch coordinates for lineage conflicts."""
    left_turn, left_branch, left_path = left
    right_turn, right_branch, right_path = right
    if left_turn is not None and right_turn is not None and left_turn != right_turn:
        return True
    if (
        left_branch is not None
        and right_branch is not None
        and left_branch != right_branch
    ):
        return True
    right_ancestors = dict(right_path)
    return any(
        turn in right_ancestors and right_ancestors[turn] != branch
        for turn, branch in left_path
    )


def _foreign_display(
    event: dict[str, Any],
    round_lineage: tuple[Any, Any, tuple[tuple[int, int], ...]],
) -> bool:
    """Return whether a display record contradicts the round's known lineage."""
    return _conflicting_lineage(_lineage(event), round_lineage)


def _eligible(event_id: int, eligible_ids: set[int] | None) -> bool:
    return eligible_ids is None or event_id in eligible_ids


def _copy_event(event: dict[str, Any]) -> dict[str, Any]:
    copied = dict(event)
    message = event.get("message")
    if isinstance(message, dict):
        copied["message"] = deepcopy(message)
    return copied


def _find_pairs(
    events: list[dict[str, Any]],
    eligible_ids: set[int] | None,
) -> dict[int, dict[str, Any]]:
    """Map valid round-start event_id → canonical end event.

    A pair is valid only when both ids are unique, positive, share lineage,
    sit on the eligible set (when provided), and no later round-start or
    user_message sits between them. Duplicate anchors or ends are ignored so
    legacy display remains recoverable.
    """
    start_index: dict[int, int] = {}
    duplicate_starts: set[int] = set()
    boundary_counts = [0]
    for index, event in enumerate(events):
        etype = event.get("type")
        boundary_counts.append(boundary_counts[-1] + (etype in _BOUNDARY_TYPES))
        if etype != "assistant_round_start":
            continue
        event_id = _positive_id(event.get("event_id"))
        if event_id is None:
            continue
        if event_id in start_index:
            duplicate_starts.add(event_id)
            continue
        start_index[event_id] = index

    ends_by_start: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    duplicate_end_ids: set[int] = set()
    seen_end_ids: set[int] = set()
    for index, event in enumerate(events):
        if event.get("type") != "assistant_message":
            continue
        if event.get("_assistant_projected"):
            continue
        end_id = _positive_id(event.get("event_id"))
        start_id = _positive_id(event.get("round_start_event_id"))
        if end_id is None or start_id is None:
            continue
        if end_id in seen_end_ids:
            duplicate_end_ids.add(end_id)
            continue
        seen_end_ids.add(end_id)
        ends_by_start.setdefault(start_id, []).append((index, event))

    pairs: dict[int, dict[str, Any]] = {}
    for start_id, candidates in ends_by_start.items():
        if start_id in duplicate_starts or start_id not in start_index:
            continue
        unique_ends = [
            (index, event)
            for index, event in candidates
            if _positive_id(event.get("event_id")) not in duplicate_end_ids
        ]
        if len(unique_ends) != 1:
            continue
        end_index, end = unique_ends[0]
        end_id = _positive_id(end.get("event_id"))
        if end_id is None:
            continue
        anchor_index = start_index[start_id]
        if end_index <= anchor_index:
            continue
        message = end.get("message")
        if (
            not isinstance(message, dict)
            or message.get("role") != "assistant"
            or not isinstance(message.get("content"), (str, list, type(None)))
        ):
            continue
        calls = message.get("tool_calls")
        if calls is not None and (
            not isinstance(calls, list)
            or not all(
                isinstance(call, dict)
                and isinstance(call.get("id"), str)
                and call["id"]
                for call in calls
            )
        ):
            continue
        anchor = events[anchor_index]
        if _lineage(anchor) != _lineage(end):
            continue
        if not (_eligible(start_id, eligible_ids) and _eligible(end_id, eligible_ids)):
            continue
        if boundary_counts[end_index] != boundary_counts[anchor_index + 1]:
            continue
        pairs[start_id] = end

    return pairs


def assistant_round_messages(
    events: list[dict], eligible_ids: set[int] | None = None
) -> dict[int, dict]:
    """Read canonical round messages without constructing a replay projection."""
    messages = {
        start: end["message"]
        for start, end in _find_pairs(events, eligible_ids).items()
    }
    messages.update(
        {
            event["event_id"]: event["message"]
            for event in events
            if event.get("_assistant_projected")
            and _eligible(event.get("event_id"), eligible_ids)
        }
    )
    return messages


def assistant_round_identities(
    events: list[dict], eligible_ids: set[int] | None = None
) -> dict[int, tuple[int, ...]]:
    """Map each round start to the event ids that carry that round.

    A valid start/end pair owns its start, the eligible streamed display pieces
    between them whose known coordinates do not contradict the round's, and the
    canonical end. A round already projected into a single event owns only
    itself.
    """
    pairs = _find_pairs(events, eligible_ids)
    anchors: dict[int, int] = {}
    ends: dict[int, int] = {}
    for index, event in enumerate(events):
        event_id = _positive_id(event.get("event_id"))
        if event_id is None:
            continue
        if event.get("type") == "assistant_round_start":
            anchors.setdefault(event_id, index)
        elif event.get("type") == "assistant_message" and not event.get(
            "_assistant_projected"
        ):
            ends.setdefault(event_id, index)
    identities: dict[int, tuple[int, ...]] = {}
    for start_id, end in pairs.items():
        # ``_find_pairs`` only pairs positive-id starts with positive-id,
        # non-projected ends, so both positions were recorded above.
        end_id = _positive_id(end.get("event_id"))
        round_lineage = _lineage(events[anchors[start_id]])
        members = [start_id]
        for event in events[anchors[start_id] + 1 : ends[end_id]]:
            if event.get("type") not in _DISPLAY_TYPES:
                continue
            if _foreign_display(event, round_lineage):
                continue
            display_id = _positive_id(event.get("event_id"))
            if display_id is not None and _eligible(display_id, eligible_ids):
                members.append(display_id)
        members.append(end_id)
        identities[start_id] = tuple(members)
    for event in events:
        if not event.get("_assistant_projected"):
            continue
        event_id = _positive_id(event.get("event_id"))
        if event_id is not None and _eligible(event_id, eligible_ids):
            identities.setdefault(event_id, (event_id,))
    return identities


def project_assistant_rounds(
    events: list[dict[str, Any]],
    eligible_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Project canonical assistant rounds onto a replay event list.

    Non-mutating. Length, order, and ``event_id`` of every original event are
    preserved. Valid round starts become ``assistant_message`` copies of the
    later canonical payload; the canonical end becomes ``assistant_round_end``;
    streamed display pieces inside the pair become ``assistant_round_display``.
    Unpaired / malformed / conflicting anchors leave display pieces intact.
    """
    pairs = _find_pairs(events, eligible_ids)
    end_ids = {
        end.get("event_id")
        for end in pairs.values()
        if _positive_id(end.get("event_id")) is not None
    }
    active_calls: set[str] | None = None
    projected: list[dict[str, Any]] = []
    for event in events:
        copied = _copy_event(event)
        event_id = _positive_id(event.get("event_id"))
        etype = event.get("type")

        if (
            etype == "assistant_round_start"
            and event_id is not None
            and event_id in pairs
        ):
            end = pairs[event_id]
            copied["type"] = "assistant_message"
            copied["message"] = deepcopy(end.get("message"))
            copied["canonical_source_event_id"] = end.get("event_id")
            copied["_assistant_projected"] = True
            active_calls = {
                call.get("id") for call in (end["message"].get("tool_calls") or [])
            }
            projected.append(copied)
            continue

        if (
            etype == "assistant_message"
            and event_id is not None
            and event_id in end_ids
        ):
            copied["type"] = "assistant_round_end"
            active_calls = None
            projected.append(copied)
            continue

        if active_calls is not None and etype in _DISPLAY_TYPES:
            calls = (
                {call.get("id") for call in (event.get("tool_calls") or [])}
                if etype == "assistant_tool_calls"
                else set()
            )
            if calls.issubset(active_calls):
                copied["type"] = "assistant_round_display"
        projected.append(copied)

    return projected


def _coerce_tool_args_to_json(args: Any) -> str:
    """Serialize tool arguments as provider-facing JSON strings."""
    if isinstance(args, str):
        return args
    if args is None:
        return "{}"
    try:
        return json.dumps(args)
    except (TypeError, ValueError):
        return "{}"


def replay_assistant_event(
    messages: list[dict],
    event: dict,
    include_metadata: bool,
    merge_image: bool = True,
    canonical_target: dict | None = None,
) -> None:
    """Restore a complete message, legacy image, or provider reasoning state."""
    match event["type"]:
        case "assistant_message":
            if not event.get("_assistant_projected"):
                return
            message = deepcopy(event["message"])
            if include_metadata:
                message["metadata"] = {
                    "event_id": event.get(
                        "canonical_source_event_id", event.get("event_id")
                    ),
                    "turn_index": event.get("turn_index"),
                    "branch_id": event.get("branch_id"),
                }
            messages.append(message)
        case "assistant_image":
            part = ImagePart(
                url=event.get("url", ""),
                detail=event.get("detail", "auto"),
                source_name=event.get("source_name"),
                source_type=event.get("source_type"),
            ).to_dict()
            if merge_image and messages and messages[-1].get("role") == "assistant":
                previous = messages[-1].get("content")
                parts = (
                    [{"type": "text", "text": previous}]
                    if isinstance(previous, str) and previous
                    else list(previous or [])
                )
                messages[-1]["content"] = [*parts, part]
            else:
                messages.append({"role": "assistant", "content": [part]})
        case "assistant_reasoning":
            target = next(
                (
                    message
                    for message in reversed(messages)
                    if message.get("role") == "assistant"
                ),
                None,
            )
            state = event.get("_kt_antigravity_content")
            if (
                state
                and target is canonical_target
                and target is not None
                and target.get("_kt_antigravity_content") == state
            ):
                return
            if state:
                target = replay_target(
                    messages, target, event["_kt_antigravity_content"]
                )
            if target is not None:
                for key in ASSISTANT_STATE_KEYS:
                    value = event.get(key)
                    if value not in (None, "", [], {}):
                        target[key] = value
