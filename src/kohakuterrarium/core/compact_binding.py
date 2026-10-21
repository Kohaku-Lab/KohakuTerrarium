"""Exact binding of captured messages to persisted event identities.

A compaction's replacement span may only swallow events that the captured
messages themselves prove. Binding is exact: a captured assistant payload must
equal exactly one canonical round on the selected path, captured users must
match one recorded user event, and captured tool results must resolve to one
final result of their own call. Folded ``tool_results``/``injected`` envelopes
bind each named job to its dispatch and its uniquely recorded final result; a
pending acknowledgement binds only its dispatch. No text proximity, no guessed
rounds, no out-of-order identities.

``resolve_capture_ids`` only resolves message identities. The caller merges
inherited summary coverage and validates the final span, so this module never
imports the summary/inheritance helpers it would otherwise cycle through.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from kohakuterrarium.session.assistant_history import (
    assistant_round_identities,
    assistant_round_messages,
)

_SUMMARY_PREFIX = "[Previous context summary (compact round "

# Message kinds that are not user input even when they carry ``role="user"``.
_NON_USER_KINDS = frozenset({"injected", "tool_results"})


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


def _is_summary(message: Any) -> bool:
    return bool(_metadata(message).get("compact_summary")) or (
        _summary_body(message) is not None
    )


def _wire(message: Any) -> dict | None:
    """The provider-facing payload of a message, without capture metadata."""
    to_dict = getattr(message, "to_dict", None)
    if not callable(to_dict):
        return None
    data = to_dict()
    if not isinstance(data, dict):
        return None
    payload = {key: value for key, value in data.items() if key != "metadata"}
    return payload or None


def _event_id(event: Mapping[str, Any]) -> int | None:
    event_id = event.get("event_id")
    return event_id if type(event_id) is int and event_id > 0 else None


def _on_path(event: Mapping[str, Any], path: set[tuple[int, int]]) -> bool:
    return (event.get("turn_index"), event.get("branch_id")) in path


def _call_identity(event: Mapping[str, Any]) -> str:
    """The job/call identity tool results share with their dispatch."""
    return str(event.get("call_id") or event.get("job_id") or "")


def _args_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return ""


def _canonical_rounds(
    events: Sequence[dict], index: Mapping[int, dict], path: set[tuple[int, int]]
) -> list[tuple[int, Any, tuple[int, ...]]]:
    """Canonical ``(start_id, payload, member_ids)`` triples on the path."""
    identities = assistant_round_identities(events)
    rounds: list[tuple[int, Any, tuple[int, ...]]] = []
    for start_id, payload in assistant_round_messages(events).items():
        anchor = index.get(start_id)
        members = identities.get(start_id)
        if anchor is None or not isinstance(payload, dict) or not members:
            continue
        if not _on_path(anchor, path):
            continue
        if any(member not in index for member in members):
            continue
        wire = {key: value for key, value in payload.items() if key != "metadata"}
        rounds.append((start_id, wire, tuple(members)))
    return rounds


def uses_canonical_rounds(events: Sequence[dict]) -> bool:
    """Presence of canonical round records; a malformed round still counts."""

    return any(
        event.get("type") == "assistant_round_start"
        or (
            event.get("type") == "assistant_message"
            and not event.get("_assistant_projected")
        )
        for event in events
    )


def _tool_declarations(captured_messages: Sequence[Any]) -> dict[str, tuple[str, str]]:
    """Provider call id → declared ``(name, arguments)`` of captured calls."""
    declared: dict[str, tuple[str, str]] = {}
    for message in captured_messages:
        if _is_summary(message) or getattr(message, "role", None) != "assistant":
            continue
        for call in getattr(message, "tool_calls", None) or ():
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or "")
            function = call.get("function")
            if call_id and isinstance(function, dict):
                declared[call_id] = (
                    str(function.get("name") or ""),
                    _args_text(function.get("arguments")),
                )
    return declared


def _call_index(path_events: Sequence[dict]) -> dict[str, list[dict]]:
    calls: dict[str, list[dict]] = {}
    for event in path_events:
        if event.get("type") not in ("tool_call", "subagent_call"):
            continue
        provider_id = event.get("tool_call_id")
        if provider_id:
            calls.setdefault(str(provider_id), []).append(event)
    return calls


def _dispatch_index(path_events: Sequence[dict]) -> dict[str, list[dict]]:
    """Job/call identity → dispatches, for envelopes that name only the job."""
    dispatches: dict[str, list[dict]] = {}
    for event in path_events:
        if event.get("type") not in ("tool_call", "subagent_call"):
            continue
        identity = _call_identity(event)
        if identity:
            dispatches.setdefault(identity, []).append(event)
    return dispatches


def _result_index(path_events: Sequence[dict]) -> dict[str, list[list[int]]]:
    """Job identity → groups of final result ids.

    An immediately repeated identical record is one result (as in
    ``summary_result_event_ids``); any other repetition keeps its own group.
    """
    groups: dict[str, list[list[int]]] = {}
    previous: dict | None = None
    for event in path_events:
        payload = {k: v for k, v in event.items() if k not in {"event_id", "ts"}}
        repeat = payload == previous
        previous = payload
        if event.get("type") != "tool_result":
            continue
        event_id = _event_id(event)
        identity = _call_identity(event)
        if event_id is None or not identity:
            continue
        bucket = groups.setdefault(identity, [])
        if repeat and bucket:
            bucket[-1].append(event_id)
        else:
            bucket.append([event_id])
    return groups


def _bind_user(message: Any, path_events: Sequence[dict]) -> int | None:
    """The one recorded user event this live user message restates.

    Folded ``injected``/``tool_results`` envelopes are not user input and an
    explicit event id never falls back to text matching.
    """
    metadata = _metadata(message)
    payload = _wire(message)
    if payload is None or metadata.get("kind") in _NON_USER_KINDS:
        return None
    hint = _pair(metadata)
    explicit = metadata.get("event_id")
    matches = [
        event
        for event in path_events
        if event.get("type") == "user_message"
        and event.get("content") == payload.get("content")
        and (hint is None or (event.get("turn_index"), event.get("branch_id")) == hint)
        and (type(explicit) is not int or _event_id(event) == explicit)
    ]
    if len(matches) != 1:
        return None
    return _event_id(matches[0])


def _bind_pending(
    message: Any,
    calls: Mapping[str, list[dict]],
    dispatches: Mapping[str, list[dict]],
    declared: Mapping[str, tuple[str, str]],
) -> tuple[set[int], int] | None:
    """A pending acknowledgement proves only the dispatch it acknowledges.

    The eventual final result is a distinct representation; it must be bound
    on its own (or stay live), so this never consumes a future result event.
    """
    metadata = _metadata(message)
    provider_id = str(getattr(message, "tool_call_id", None) or "")
    job_id = str(metadata.get("job_id") or "")
    matches = calls.get(provider_id, []) if provider_id else []
    if not provider_id and job_id:
        matches = dispatches.get(job_id, [])
    if len(matches) != 1:
        return None
    dispatch = matches[0]
    if job_id and _call_identity(dispatch) != job_id:
        return None
    if provider_id and provider_id in declared:
        name, arguments = declared[provider_id]
        if str(dispatch.get("name") or "") != name:
            return None
        if _args_text(dispatch.get("tool_call_arguments") or dispatch.get("args")) != (
            arguments
        ):
            return None
    event_id = _event_id(dispatch)
    if event_id is None:
        return None
    return {event_id}, event_id


def _bind_result(
    message: Any,
    index: Mapping[int, dict],
    path: set[tuple[int, int]],
    calls: Mapping[str, list[dict]],
    results: Mapping[str, list[list[int]]],
    settled: Mapping[str, list[list[int]]],
    declared: Mapping[str, tuple[str, str]],
) -> tuple[set[int], int | None] | None:
    """The dispatch, and the on-path final results, of this captured tool message.

    A settled result is recorded by the turn that observed it, which may sit on
    a branch the selected view has left, so an explicit ``result_event_id`` is
    validated against every recorded result while only on-path ids enter the
    span. The dispatch then supplies the ordering anchor: a late result id must
    not contradict the user sequence the messages still carry.
    """
    metadata = _metadata(message)
    provider_id = str(
        getattr(message, "tool_call_id", None) or metadata.get("job_id") or ""
    )
    dispatches = calls.get(provider_id, []) if provider_id else []
    if len(dispatches) > 1:
        return None
    dispatch = dispatches[0] if dispatches else None
    if dispatch is not None and provider_id in declared:
        name, arguments = declared[provider_id]
        if str(dispatch.get("name") or "") != name:
            return None
        if _args_text(dispatch.get("tool_call_arguments") or dispatch.get("args")) != (
            arguments
        ):
            return None
    identities = {provider_id} - {""}
    if dispatch is not None:
        identities.add(_call_identity(dispatch))
    explicit = metadata.get("result_event_id")
    if type(explicit) is int and explicit > 0:
        event = index.get(explicit)
        if event is None or event.get("type") != "tool_result":
            return None
        identity = _call_identity(event)
        if not identity or identity not in identities:
            return None
        bound = {explicit}
        for group in settled.get(identity, ()):
            if explicit in group:
                bound.update(group)
    else:
        target = _call_identity(dispatch) if dispatch is not None else provider_id
        groups = results.get(target, []) if target else []
        if len(groups) != 1:
            return None
        bound = set(groups[0])
    if dispatch is not None:
        bound.add(dispatch["event_id"])
    anchor = dispatch["event_id"] if dispatch is not None else None
    proven = {
        event_id
        for event_id in bound
        if (recorded := index.get(event_id)) is not None and _on_path(recorded, path)
    }
    if anchor is None:
        if not proven:
            return None
        anchor = max(proven)
    return proven, anchor


def _bind_folded(
    message: Any,
    index: Mapping[int, dict],
    path: set[tuple[int, int]],
    dispatches: Mapping[str, list[dict]],
    settled: Mapping[str, list[list[int]]],
) -> tuple[set[int], int] | None:
    """A folded result envelope proves each named job's dispatch and result.

    Each job must resolve to exactly one recorded dispatch and one uniquely
    recorded final result group; only ids that are actually on the selected
    path enter the span, so a result settled onto a sibling branch never widens
    it.
    """
    job_ids = [str(job) for job in _metadata(message).get("job_ids") or () if job]
    if not job_ids:
        return None
    proven: set[int] = set()
    anchors: list[int] = []
    for job in job_ids:
        matches = dispatches.get(job, [])
        if len(matches) != 1:
            return None
        groups = settled.get(job, [])
        if len(groups) != 1:
            return None
        bound = set(groups[0])
        dispatch_id = _event_id(matches[0])
        if dispatch_id is not None:
            bound.add(dispatch_id)
        on_path = {
            event_id
            for event_id in bound
            if (recorded := index.get(event_id)) is not None
            and _on_path(recorded, path)
        }
        if not on_path:
            return None
        proven |= on_path
        anchors.append(max(on_path))
    return proven, max(anchors)


def _candidate_user_ids(message: Any, path_events: Sequence[dict]) -> set[int]:
    """Every recorded user event whose content restates this message."""
    content = (_wire(message) or {}).get("content")
    return {
        event_id
        for event in path_events
        if event.get("type") == "user_message"
        and event.get("content") == content
        and (event_id := _event_id(event)) is not None
    }


def _retained_result_ids(
    message: Any,
    calls: Mapping[str, list[dict]],
    results: Mapping[str, list[list[int]]],
) -> set[int]:
    """Final result ids a retained tool message must keep live.

    The dispatch is deliberately excluded: a captured pending acknowledgement
    may already have proven it while the final result itself stays live.
    """
    metadata = _metadata(message)
    provider_id = str(
        getattr(message, "tool_call_id", None) or metadata.get("job_id") or ""
    )
    ids: set[int] = set()
    for dispatch in calls.get(provider_id, []) if provider_id else ():
        for group in results.get(_call_identity(dispatch), ()):
            ids.update(group)
    if provider_id:
        for group in results.get(provider_id, ()):
            ids.update(group)
    return ids


def _folded_result_ids(
    message: Any, results: Mapping[str, list[list[int]]]
) -> set[int]:
    """Final result ids a retained folded envelope must keep live."""
    ids: set[int] = set()
    for job in _metadata(message).get("job_ids") or ():
        for group in results.get(str(job), ()):
            ids.update(group)
    return ids


def _restated_input(
    event: Mapping[str, Any], index: Mapping[int, dict], proven: set[int]
) -> bool:
    """A user_input envelope echoing a proven user_message of the same turn."""
    pair = (event.get("turn_index"), event.get("branch_id"))
    return any(
        (proven_event := index.get(event_id)) is not None
        and proven_event.get("type") == "user_message"
        and proven_event.get("content") == event.get("content")
        and (proven_event.get("turn_index"), proven_event.get("branch_id")) == pair
        for event_id in proven
    )


def resolve_capture_ids(
    events: Sequence[dict],
    path: set[tuple[int, int]],
    captured_messages: Sequence[Any],
    retained_messages: Sequence[Any] = (),
    *,
    retain_latest_user: bool = False,
) -> tuple[set[int], set[int]] | None:
    """Event ids the captured messages prove, and ids retained ones keep live.

    Every captured message must resolve to exactly one recorded identity: the
    one canonical round with the identical payload (all of its start, display
    and end members are then proven), one recorded user event, one pending
    dispatch, or one folded job's uniquely recorded final result and dispatch.
    Resolved neighbours must additionally keep recorded order, so a repeated or
    reordered payload refuses the span instead of widening it.

    Returns ``(proven_ids, excluded_ids)`` or ``None`` when anything is
    ambiguous. ``excluded_ids`` are identities a retained message must keep
    live; the caller drops any span that would swallow them.
    """
    if not path:
        return None
    index: dict[int, dict] = {}
    for event in events:
        event_id = _event_id(event)
        if event_id is None:
            continue
        if event_id in index:
            # A duplicated id makes every identity lookup ambiguous.
            return None
        index[event_id] = event
    rounds = _canonical_rounds(events, index, path)
    if not rounds:
        return None
    path_events = [event for event in events if _on_path(event, path)]
    calls = _call_index(path_events)
    dispatches = _dispatch_index(path_events)
    results = _result_index(path_events)
    # Results are matched by identity over the whole log; a settled one lives
    # wherever the turn that observed it was recorded.
    settled = _result_index(events)
    declared = _tool_declarations(captured_messages)

    # Captured messages are summarized, retained ones stay live; both keep
    # conversation order so their identities bracket the rounds.
    sequence = [(True, message) for message in captured_messages]
    sequence += [(False, message) for message in retained_messages]
    proven: set[int] = set()
    retained_user: set[int] = set()
    blocked: set[int] = set()
    anchors: list[tuple[int, int]] = []
    assistants: list[int] = []
    last_user: int | None = None
    for position, (captured, message) in enumerate(sequence):
        if _is_summary(message):
            continue
        metadata = _metadata(message)
        role = getattr(message, "role", None)
        if role == "user":
            if captured:
                if (
                    metadata.get("kind") == "injected"
                    and metadata.get("origin_known") is False
                    and not metadata.get("job_ids")
                ):
                    continue
                if metadata.get("kind") in _NON_USER_KINDS:
                    folded = _bind_folded(message, index, path, dispatches, settled)
                    if folded is None:
                        return None
                    ids, anchor = folded
                    proven |= ids
                    anchors.append((position, anchor))
                    continue
                user_id = _bind_user(message, path_events)
                if user_id is None or (last_user is not None and user_id <= last_user):
                    # Captured identities must keep recorded order.
                    return None
                proven.add(user_id)
                last_user = user_id
                anchors.append((position, user_id))
                continue
            if metadata.get("kind") in _NON_USER_KINDS:
                blocked |= _folded_result_ids(message, results)
                continue
            ids = _candidate_user_ids(message, path_events)
            retained_user |= ids
            if len(ids) == 1:
                anchors.append((position, next(iter(ids))))
        elif role == "tool":
            if captured:
                if metadata.get("tool_result_phase") == "pending":
                    bound = _bind_pending(message, calls, dispatches, declared)
                else:
                    bound = _bind_result(
                        message, index, path, calls, results, settled, declared
                    )
                if bound is None:
                    return None
                ids, anchor = bound
                proven |= ids
                anchors.append((position, anchor))
                continue
            ids = _retained_result_ids(message, calls, results)
            blocked |= ids
            if len(ids) == 1:
                anchors.append((position, next(iter(ids))))
        elif role == "assistant":
            assistants.append(position)

    used: set[int] = set()
    resolved_end: int | None = None
    for position in assistants:
        captured, message = sequence[position]
        payload = _wire(message)
        recorded = _metadata(message).get("event_id")
        recorded = recorded if type(recorded) is int and recorded > 0 else None
        after = max((eid for at, eid in anchors if at < position), default=None)
        before = min((eid for at, eid in anchors if at > position), default=None)
        candidates = [
            (start, members)
            for start, round_payload, members in rounds
            if round_payload == payload
            and (recorded is None or members[-1] == recorded)
            and (after is None or start > after)
            and (before is None or start < before)
            and (resolved_end is None or start > resolved_end)
        ]
        if not candidates and any(
            round_payload == payload and (recorded is None or members[-1] == recorded)
            for _start, round_payload, members in rounds
        ):
            # The payload restates a recorded round that the resolved order
            # places elsewhere: contradictory evidence proves nothing.
            return None
        if not captured:
            for _start, members in candidates:
                blocked |= set(members)
            continue
        if len(candidates) != 1:
            return None
        start, members = candidates[0]
        if start in used:
            # Two captured messages cannot restate one recorded round.
            return None
        used.add(start)
        resolved_end = max(members)
        proven |= set(members)

    if retain_latest_user and last_user is not None:
        retained_user.add(last_user)
    proven -= retained_user
    return proven, retained_user | blocked
