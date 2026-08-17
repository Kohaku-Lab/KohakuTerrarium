"""Lightweight per-event timeline spans for the Session Viewer trace tab.

The lane overview needs one small record per event across every turn —
fetching full event payloads (which carry content/output text) would be
far too heavy, so this module projects events down to timing fields only.
"""

from typing import Any

from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.session.history import dedupe_adjacent_duplicate_events
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.rollups import (
    ERROR_EVENT_TYPES,
    _event_turn_index,
    _subagent_failed,
)

#: Fields copied onto each span. Everything else (content, output, …) is
#: dropped so the payload stays proportional to event count, not bytes.
_DURATION_KEYS = ("duration_ms", "elapsed_ms")

#: Types that carry no timeline signal (pure counters / noise).
_NOISE_TYPES = frozenset({"cache_stats"})

#: Result event types that close a ``tool_call`` span (by ``call_id``).
_TOOL_RESULT_TYPES = frozenset({"tool_result", "tool_error"})
#: Result event types that close a ``subagent_call`` span (by ``job_id``).
_JOB_RESULT_TYPES = frozenset(
    {"subagent_result", "subagent_error", "background_result"}
)


def _span_from_event(evt: dict) -> dict[str, Any]:
    duration = None
    for key in _DURATION_KEYS:
        value = evt.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            duration = value
            break
    span: dict[str, Any] = {
        "eid": evt.get("event_id"),
        "type": evt.get("type"),
        "ts": evt.get("ts"),
        "dur": duration,
        "turn": _event_turn_index(evt),
        "err": evt.get("type") in ERROR_EVENT_TYPES or _subagent_failed(evt),
    }
    label = evt.get("tool") or evt.get("name") or evt.get("tool_name")
    if label:
        span["label"] = str(label)[:80]
    return span


def _set_span_dur(span: dict, end_ts: float) -> None:
    """Fill ``dur`` from a paired end timestamp; never overwrites, never negative."""
    if span.get("dur") is not None:
        return
    start = span.get("ts")
    if not isinstance(start, (int, float)) or not isinstance(end_ts, (int, float)):
        return
    if end_ts > start:
        span["dur"] = round((end_ts - start) * 1000, 3)


def _pair_durations(paired: list[tuple[dict, dict]]) -> None:
    """Derive durations for start/end event pairs, patching spans in place.

    The harness emits point events for most work; pairing recovers real
    spans so the duration/actual projections stay meaningful:

    - ``tool_call`` ↔ ``tool_result``/``tool_error`` via ``call_id``
    - ``subagent_call`` ↔ ``subagent_result``/``background_result`` via
      ``job_id`` (background jobs overlap — that overlap is the point)
    - ``processing_start`` ↔ ``processing_end`` per turn (model busy time)
    - ``tool_wait`` carries ``wait_ms`` directly
    """
    pending_tools: dict[str, dict] = {}
    pending_jobs: dict[str, dict] = {}
    pending_processing: dict[int, dict] = {}
    for evt, span in paired:
        etype = evt.get("type")
        if etype == "tool_call":
            call_id = evt.get("call_id")
            if call_id:
                pending_tools[str(call_id)] = span
        elif etype in _TOOL_RESULT_TYPES:
            start = pending_tools.pop(str(evt.get("call_id") or ""), None)
            if start is not None:
                _set_span_dur(start, evt.get("ts"))
        elif etype == "subagent_call":
            job_id = evt.get("job_id")
            if job_id:
                pending_jobs[str(job_id)] = span
        elif etype in _JOB_RESULT_TYPES:
            start = pending_jobs.pop(str(evt.get("job_id") or ""), None)
            if start is not None:
                duration_s = evt.get("duration")
                if isinstance(duration_s, (int, float)) and duration_s > 0:
                    start["dur"] = round(duration_s * 1000, 3)
                else:
                    _set_span_dur(start, evt.get("ts"))
        elif etype == "processing_start":
            turn = _event_turn_index(evt)
            if turn is not None:
                pending_processing[turn] = span
        elif etype == "processing_end":
            turn = _event_turn_index(evt)
            start = pending_processing.pop(turn, None)
            if start is not None:
                _set_span_dur(start, evt.get("ts"))
        elif etype == "tool_wait":
            wait_ms = evt.get("wait_ms")
            if isinstance(wait_ms, (int, float)) and wait_ms > 0:
                span["dur"] = wait_ms


def build_timeline_payload(
    store: SessionStore,
    session_name: str,
    *,
    agent: str | None,
    limit: int,
) -> dict[str, Any]:
    """Return compact timing spans for one agent, most recent first-biased.

    When the event count exceeds ``limit`` the *latest* spans are kept and
    ``truncated`` is set — the overview is most useful for recent history,
    and the per-turn event pages remain available for the elided prefix.
    """
    meta = store.load_meta()
    main_agents = list(meta.get("agents") or [])
    attached_namespaces = [
        e["namespace"] for e in store.discover_attached_agents() if e.get("namespace")
    ]
    known_agents = main_agents + [
        n for n in attached_namespaces if n not in main_agents
    ]
    if agent is None:
        default = meta.get("viewer_default_agent")
        if isinstance(default, str) and default in known_agents:
            agent = default
        elif main_agents:
            agent = main_agents[0]
        else:
            raise NotFoundError(f"Session has no agents: {session_name}")
    elif agent not in known_agents:
        raise NotFoundError(f"Agent not found in session: {agent}")

    rows = dedupe_adjacent_duplicate_events(store.get_events(agent))
    paired = [
        (evt, _span_from_event(evt))
        for evt in rows
        if evt.get("type") not in _NOISE_TYPES
    ]
    _pair_durations(paired)
    spans = [span for _evt, span in paired]
    truncated = len(spans) > limit
    if truncated:
        spans = spans[-limit:]

    return {
        "session_name": session_name,
        "agent": agent,
        "spans": spans,
        "count": len(spans),
        "limit": limit,
        "truncated": truncated,
    }


def merge_timeline_payloads(
    per_member: list[tuple[str, dict[str, Any]]],
    session_name: str,
    *,
    limit: int,
) -> dict[str, Any]:
    """Merge cluster timeline spans into one chronological sequence.

    Span IDs are monotonic only within one store, so member identity is part
    of the dedupe key and the timestamp tie-breaker, mirroring event merging
    in the API layer. When the combined sequence exceeds ``limit`` the latest
    spans are kept, matching single-member truncation.
    """
    spans: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for member_sid, payload in per_member:
        for span in payload.get("spans") or []:
            eid = span.get("eid")
            key = (member_sid, int(eid) if isinstance(eid, int) else -1)
            if key in seen:
                continue
            seen.add(key)
            tagged = dict(span)
            tagged.setdefault("member_sid", member_sid)
            spans.append(tagged)
    spans.sort(
        key=lambda s: (
            float(s.get("ts") or 0.0),
            str(s.get("member_sid") or ""),
            int(s.get("eid") or 0),
        )
    )
    truncated = len(spans) > limit
    if truncated:
        spans = spans[-limit:]
    return {
        "session_name": session_name,
        "agent": None,
        "spans": spans,
        "count": len(spans),
        "limit": limit,
        "truncated": truncated,
    }
