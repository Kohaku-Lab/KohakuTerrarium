"""Complete a proven checkpoint with its minimal missing dependency groups.

A proven checkpoint can omit a tool call only when the summary proves it
consumed it. When the call, its canonical assistant round and its single result
still exist in raw history, the missing group is restored instead of refusing
the whole checkpoint. Anything ambiguous stays a refusal.
"""

from typing import Any

from kohakuterrarium.session.assistant_history import (
    assistant_round_identities,
    assistant_round_messages,
)
from kohakuterrarium.session.history import replay_conversation


def _provider_id(event: dict) -> str:
    return str(event.get("tool_call_id") or event.get("call_id") or "")


def _job_id(event: dict) -> str:
    return str(event.get("call_id") or event.get("job_id") or "")


def _result_index(
    events: list[dict], jobs_by_provider: dict[str, set[str]]
) -> dict[str, list[dict]] | None:
    """Map each canonical call to its uniquely owned settled result."""
    results: dict[str, list[dict]] = {}
    for event in events:
        if event.get("type") == "tool_result" and _job_id(event):
            results.setdefault(_job_id(event), []).append(event)
    by_call: dict[str, list[dict]] = {}
    claimed: dict[str, str] = {}
    for provider, jobs in jobs_by_provider.items():
        if len(jobs) != 1:
            return None
        job = next(iter(jobs))
        if job in claimed:
            # Two providers naming one job share a single result; replay would
            # pair it with one call and leave the other unanswered.
            return None
        claimed[job] = provider
        by_call[provider] = results.get(job, [])
    return by_call


def _pair(event: dict) -> tuple[Any, Any]:
    return event.get("turn_index"), event.get("branch_id")


def _group_rounds(
    gaps: list[dict],
    rounds: dict[int, dict],
    round_ids: dict[int, tuple[int, ...]],
    target_event_id: int,
    present_call_ids: set[str],
    represented_event_ids: set[int],
    cutoff: int,
    events: list[dict],
) -> dict[int, list[str]] | None:
    """Map each missing call onto the one canonical round that declared it.

    A round is restored whole or not at all, and only when *every* call it
    declares is itself an uncovered gap. A round that the prefix partly holds,
    whose own start, display pieces or canonical end the summary already covers,
    or that sits inside the cleared cutoff is refused: expanding it would
    resurrect represented content or duplicate a call.
    """
    owners: dict[str, list[int]] = {}
    for start, message in rounds.items():
        for call in message.get("tool_calls") or []:
            owners.setdefault(str(call.get("id")), []).append(start)
    anchors = {event.get("event_id"): event for event in events}
    gap_providers = {_provider_id(event) for event in gaps}
    groups: dict[int, list[str]] = {}
    for event in gaps:
        starts = owners.get(_provider_id(event), [])
        if len(starts) != 1:
            return None
        start = starts[0]
        members = round_ids.get(start)
        if not members or any(member >= target_event_id for member in members):
            # The whole round, not only its start, must precede the target it
            # is restored for.
            return None
        if any(
            member in represented_event_ids or member <= cutoff for member in members
        ):
            # A covered start, display piece or canonical end means the summary
            # already holds this round's content.
            return None
        call_ids = [
            str(call.get("id")) for call in rounds[start].get("tool_calls") or []
        ]
        if present_call_ids.intersection(call_ids):
            return None
        if set(call_ids) - gap_providers:
            # A sibling call is already represented (consumed or cleared); the
            # round cannot be restored without resurrecting it.
            return None
        anchor = anchors.get(start) or {}
        if _pair(anchor) != _pair(event):
            # The round must belong to the same selected turn/branch as the
            # dispatch it is restored for.
            return None
        groups[start] = call_ids
    return groups


def repair_gap_messages(
    gaps: list[dict],
    events: list[dict],
    *,
    target_event_id: int,
    present_call_ids: set[str] | None = None,
    represented_event_ids: set[int] | None = None,
    cutoff: int = 0,
    eligible_ids: set[int] | None = None,
) -> list[dict[str, Any]] | None:
    """Replay the missing rounds and results, or ``None`` when unprovable.

    A round is restored whole: its canonical assistant payload keeps the
    provider state and every declared call, and each of those calls must have
    exactly one result that precedes the target.
    """
    if not gaps:
        return []
    jobs_by_provider: dict[str, set[str]] = {}
    for event in events:
        if event.get("type") == "tool_call" and _provider_id(event) and _job_id(event):
            jobs_by_provider.setdefault(_provider_id(event), set()).add(_job_id(event))
    rounds = assistant_round_messages(events, eligible_ids)
    groups = _group_rounds(
        gaps,
        rounds,
        assistant_round_identities(events, eligible_ids),
        target_event_id,
        present_call_ids or set(),
        represented_event_ids or set(),
        cutoff,
        events,
    )
    if groups is None:
        return None
    by_call = _result_index(events, jobs_by_provider)
    if by_call is None:
        return None
    provider_by_job = {
        job: provider for provider, jobs in jobs_by_provider.items() for job in jobs
    }
    repaired: list[dict[str, Any]] = []
    anchors = {event.get("event_id"): event for event in events}
    for start in sorted(groups):
        canonical = rounds[start]
        anchor = anchors.get(start) or {}
        sub_events = [
            {
                "event_id": start,
                "type": "assistant_message",
                "message": canonical,
                "_assistant_projected": True,
                "turn_index": anchor.get("turn_index"),
                "branch_id": anchor.get("branch_id"),
            }
        ]
        for call_id in groups[start]:
            matches = by_call.get(call_id, [])
            if len(matches) != 1 or matches[0].get("event_id", 0) >= target_event_id:
                return None
            sub_events.append(matches[0])
        for message in replay_conversation(sub_events, include_metadata=True):
            if message.get("role") == "tool":
                # Pair the restored result with the round's own call id, not the
                # job id replay falls back to when no dispatch event is present.
                job = (message.get("metadata") or {}).get("job_id")
                provider = provider_by_job.get(str(job))
                if provider is None:
                    return None
                message["tool_call_id"] = provider
            repaired.append(message)
    return repaired
