"""Filtered + paginated events for one agent in a saved session.

Verbatim port of ``_session_viewer.py:build_events_payload`` and the
``_parse_type_filter`` helper it uses.
"""

from typing import Any

from fastapi import HTTPException

from kohakuterrarium.session.store import SessionStore


def parse_type_filter(types: str | None) -> set[str] | None:
    """Comma-separated event-type allowlist; ``None`` = no filter."""
    if not types:
        return None
    parts = [t.strip() for t in types.split(",") if t.strip()]
    return set(parts) if parts else None


def build_events_payload(
    store: SessionStore,
    session_name: str,
    *,
    agent: str | None,
    turn_index: int | None,
    types: str | None,
    from_ts: float | None,
    to_ts: float | None,
    limit: int,
    cursor: int | None,
) -> dict[str, Any]:
    """Filtered events for one agent.

    Cursor is the last seen ``event_id``. Returns ``next_cursor`` =
    ``event_id`` of the last row when more rows remain, else ``None``.
    The agent argument is required so this stays O(events_for_one_agent)
    — cross-agent enumeration is a separate concern (see ``/turns``).
    """
    meta = store.load_meta()
    if agent is None:
        all_agents = list(meta.get("agents") or [])
        if not all_agents:
            raise HTTPException(404, f"Session has no agents: {session_name}")
        agent = all_agents[0]
    elif agent not in (meta.get("agents") or []):
        raise HTTPException(404, f"Agent not found in session: {agent}")

    type_set = parse_type_filter(types)
    rows = store.get_events(agent)

    out: list[dict] = []
    for ev in rows:
        if cursor is not None and int(ev.get("event_id") or 0) <= cursor:
            continue
        if turn_index is not None and ev.get("turn_index") != turn_index:
            continue
        if type_set is not None and ev.get("type") not in type_set:
            continue
        if from_ts is not None and float(ev.get("ts") or 0) < from_ts:
            continue
        if to_ts is not None and float(ev.get("ts") or 0) > to_ts:
            continue
        out.append(ev)
        if len(out) >= limit:
            break

    next_cursor: int | None = None
    if out and len(out) >= limit:
        last = out[-1].get("event_id")
        if isinstance(last, int):
            next_cursor = last

    return {
        "session_name": session_name,
        "agent": agent,
        "events": out,
        "count": len(out),
        "limit": limit,
        "next_cursor": next_cursor,
        "filters": {
            "turn_index": turn_index,
            "types": sorted(type_set) if type_set else None,
            "from_ts": from_ts,
            "to_ts": to_ts,
        },
    }
