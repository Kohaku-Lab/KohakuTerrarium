"""Filtered + paginated events for one agent in a saved session.

Builds one-agent event pages with cursor, turn, type, and timestamp filters.
"""

from typing import Any

from kohakuterrarium.errors import NotFoundError
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
    """Return a filtered event page for one agent.

    ``cursor`` is the last observed event ID. A full page returns its final ID
    as ``next_cursor``; shorter pages return ``None``. Restricting the query to
    one agent keeps work proportional to that namespace's events.
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
