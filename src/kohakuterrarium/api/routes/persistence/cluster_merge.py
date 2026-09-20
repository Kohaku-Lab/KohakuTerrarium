"""Cross-member payload merges for clustered session views.

Each member store yields its own payload slice; these pure functions combine
them into the single response shape each viewer endpoint returns. Merging is
in-memory work over already-loaded payloads, so routes can run it on the event
loop after dispatching member loads onto worker or affinity threads.
"""

from typing import Any


def merge_tree(
    per_member: list[tuple[str, dict[str, Any]]], session_name: str
) -> dict[str, Any]:
    """Merge cluster tree nodes and edges without duplicate identities.

    Each member contributes its attached-agent slice and normally disjoint fork
    lineage. Nodes use first-write-wins deduplication by ``id`` so a creature
    attached to multiple members appears once; edges are unique by
    ``(from, to, type)``.
    """
    nodes: list[dict[str, Any]] = []
    seen_node_ids: set[str] = set()
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[Any, Any, Any]] = set()
    primary_id = session_name
    primary_set = False
    for _member_sid, payload in per_member:
        for node in payload.get("nodes", []):
            nid = node.get("id")
            if nid is None or nid in seen_node_ids:
                continue
            seen_node_ids.add(nid)
            nodes.append(node)
        for edge in payload.get("edges", []):
            key = (edge.get("from"), edge.get("to"), edge.get("type"))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(edge)
        if payload.get("session_id") and not primary_set:
            primary_id = str(payload.get("session_id"))
            primary_set = True
    return {
        "session_name": session_name,
        "session_id": primary_id,
        "nodes": nodes,
        "edges": edges,
    }


def merge_summary(
    per_member: list[tuple[str, dict[str, Any]]], session_name: str
) -> dict[str, Any]:
    """Aggregate Overview statistics across cluster members.

    Counts and token totals are additive. Agent and classified-turn lists are
    unions, while hot turns are ranked by cost or token volume and limited to
    five. Session identity and configuration fields come from the first
    resolved member to keep one stable overview identity.
    """
    if not per_member:
        return {"session_name": session_name, "agents": [], "totals": {}}
    base = per_member[0][1]
    agents: list[str] = list(base.get("agents") or [])
    seen_agents = set(agents)
    totals_acc = {
        "turns": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "cost_seen": False,
        "tool_calls": 0,
        "errors": 0,
        "compacts": 0,
        "forks": 0,
        "attached_agents": 0,
    }
    error_turns: list[int] = []
    compact_turns: list[int] = []
    hot_turns: list[dict[str, Any]] = []
    for _member_sid, payload in per_member:
        for a in payload.get("agents") or []:
            if a not in seen_agents:
                seen_agents.add(a)
                agents.append(a)
        t = payload.get("totals") or {}
        totals_acc["turns"] += int(t.get("turns") or 0)
        tk = t.get("tokens") or {}
        totals_acc["prompt_tokens"] += int(tk.get("prompt") or 0)
        totals_acc["completion_tokens"] += int(tk.get("completion") or 0)
        totals_acc["cached_tokens"] += int(tk.get("cached") or 0)
        c = t.get("cost_usd")
        if c is not None:
            try:
                totals_acc["cost_usd"] += float(c)
                totals_acc["cost_seen"] = True
            except (TypeError, ValueError):
                pass
        totals_acc["tool_calls"] += int(t.get("tool_calls") or 0)
        totals_acc["errors"] += int(t.get("errors") or 0)
        totals_acc["compacts"] += int(t.get("compacts") or 0)
        totals_acc["forks"] += int(t.get("forks") or 0)
        totals_acc["attached_agents"] += int(t.get("attached_agents") or 0)
        error_turns.extend(payload.get("error_turns") or [])
        compact_turns.extend(payload.get("compact_turns") or [])
        hot_turns.extend(payload.get("hot_turns") or [])

    def _hot_key(r: dict) -> tuple[int, float]:
        c = r.get("cost_usd")
        if c is not None:
            try:
                return (0, float(c))
            except (TypeError, ValueError):
                pass
        return (1, float(r.get("tokens_in") or 0) + float(r.get("tokens_out") or 0))

    hot_turns.sort(key=_hot_key, reverse=True)
    return {
        "session_name": session_name,
        "session_id": str(base.get("session_id") or session_name),
        "format_version": base.get("format_version"),
        "status": base.get("status"),
        "created_at": base.get("created_at"),
        "last_active": base.get("last_active"),
        "config_type": base.get("config_type"),
        "config_path": base.get("config_path"),
        "agents": agents,
        "lineage": base.get("lineage") or {},
        "totals": {
            "turns": totals_acc["turns"],
            "tokens": {
                "prompt": totals_acc["prompt_tokens"],
                "completion": totals_acc["completion_tokens"],
                "cached": totals_acc["cached_tokens"],
            },
            "cost_usd": totals_acc["cost_usd"] if totals_acc["cost_seen"] else None,
            "tool_calls": totals_acc["tool_calls"],
            "errors": totals_acc["errors"],
            "compacts": totals_acc["compacts"],
            "forks": totals_acc["forks"],
            "attached_agents": totals_acc["attached_agents"],
        },
        "hot_turns": hot_turns[:5],
        "error_turns": sorted(set(error_turns)),
        "compact_turns": sorted(set(compact_turns)),
    }


def merge_turns(
    per_member: list[tuple[str, dict[str, Any]]],
    session_name: str,
    *,
    limit: int,
    offset: int,
    from_turn: int | None,
    to_turn: int | None,
) -> dict[str, Any]:
    """Merge cluster turn rows into a stable paginated sequence.

    Turn indices are member-local, so agent or member identity breaks ties.
    Pagination is applied after merging to make ``offset`` and ``limit`` refer
    to the combined result rather than to each member independently.
    """
    rows: list[dict[str, Any]] = []
    for member_sid, payload in per_member:
        for row in payload.get("turns") or []:
            tagged = dict(row)
            tagged.setdefault("member_sid", member_sid)
            rows.append(tagged)
    rows.sort(
        key=lambda r: (
            int(r.get("turn_index") or 0),
            str(r.get("agent") or r.get("member_sid") or ""),
        )
    )
    total = sum(
        int(payload.get("total", len(payload.get("turns") or [])))
        for _member_sid, payload in per_member
    )
    page = rows[offset : offset + limit]
    return {
        "session_name": session_name,
        "agent": None,
        "aggregate": True,
        "turns": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "from_turn": from_turn,
        "to_turn": to_turn,
    }


def merge_events(
    per_member: list[tuple[str, dict[str, Any]]],
    session_name: str,
    *,
    limit: int,
) -> dict[str, Any]:
    """Merge cluster event rows into a stable chronological sequence.

    Event IDs are monotonic only within one store, so member identity is part
    of both the deduplication key and the timestamp tie-breaker. The requested
    limit applies to the combined sequence.
    """
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for member_sid, payload in per_member:
        for ev in payload.get("events") or []:
            eid = ev.get("event_id")
            key = (member_sid, int(eid) if isinstance(eid, int) else -1)
            if key in seen:
                continue
            seen.add(key)
            tagged = dict(ev)
            tagged.setdefault("member_sid", member_sid)
            rows.append(tagged)
    rows.sort(
        key=lambda e: (
            float(e.get("ts") or 0.0),
            str(e.get("member_sid") or ""),
            int(e.get("event_id") or 0),
        )
    )
    page = rows[:limit]
    return {
        "session_name": session_name,
        "agent": None,
        "events": page,
        "count": len(page),
        "limit": limit,
        "next_cursor": None,
        "filters": {
            "turn_index": None,
            "types": None,
            "from_ts": None,
            "to_ts": None,
        },
    }
