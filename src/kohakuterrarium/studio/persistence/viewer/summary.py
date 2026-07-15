"""Overview-tab summary builder for the Session Viewer.

Builds overview totals, hot turns, and event-derived counters for persisted
sessions.
"""

from collections.abc import Iterable
from typing import Any

from kohakuterrarium.errors import NotFoundError
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.rollups import (
    ERROR_EVENT_TYPES,
    aggregate_turn_rollups,
    graph_total_usage,
    rollups_or_derived,
)


def _subagent_failed(evt: dict) -> bool:
    final_state = str(evt.get("final_state") or "").lower()
    return bool(
        evt.get("type") == "subagent_result"
        and (
            evt.get("success") is False
            or evt.get("error")
            or evt.get("interrupted")
            or evt.get("cancelled")
            or final_state in {"error", "interrupted", "cancelled"}
        )
    )


def _agents_for_summary(
    meta: dict[str, Any], store: SessionStore, requested: str | None
) -> list[str]:
    """Select and order agent namespaces for the overview.

    A requested agent must belong to the session. Without one, all main and
    attached namespaces are included, with ``viewer_default_agent`` first.
    ``load_meta`` already includes event-discovered creatures in ``agents``.
    """
    main_agents = list(meta.get("agents") or [])
    attached_namespaces = [
        e["namespace"] for e in store.discover_attached_agents() if e.get("namespace")
    ]
    known_agents = main_agents + [
        n for n in attached_namespaces if n not in main_agents
    ]
    if requested is None:
        default = meta.get("viewer_default_agent")
        if isinstance(default, str) and default in known_agents:
            ordered = [default] + [a for a in known_agents if a != default]
            return ordered
        return known_agents
    if requested not in known_agents:
        raise NotFoundError(f"Agent not found in session: {requested}")
    return [requested]


def _aggregate_rollups(
    rollups: Iterable[dict], *, count_by_agent: bool = False
) -> dict[str, Any]:
    """Sum rollup rows while preserving the selected turn-count semantics.

    Agent views count unique turn indexes. Session-wide views count each
    non-sub-agent controller contribution to preserve multi-agent loop totals.
    """
    prompt = completion = cached = 0
    cost_usd = 0.0
    cost_seen = False
    rows = list(rollups)
    seen_turns: set[int] = set()
    for r in rows:
        ti = r.get("turn_index")
        if isinstance(ti, int) and ti > 0:
            seen_turns.add(ti)
        prompt += int(r.get("tokens_in") or 0)
        completion += int(r.get("tokens_out") or 0)
        cached += int(r.get("tokens_cached") or 0)
        c = r.get("cost_usd")
        if c is not None:
            try:
                cost_usd += float(c)
                cost_seen = True
            except (TypeError, ValueError):
                pass
    if count_by_agent:
        turns = 0
        for row in rows:
            breakdown = row.get("breakdown") or []
            if breakdown:
                turns += sum(1 for item in breakdown if item.get("kind") != "subagent")
            else:
                turns += 1
    else:
        turns = len(seen_turns) or len(rows)
    return {
        "turns": turns,
        "tokens": {"prompt": prompt, "completion": completion, "cached": cached},
        "cost_usd": cost_usd if cost_seen else None,
    }


def _scan_events_for_summary(events: list[dict]) -> dict[str, Any]:
    """Count tool calls / errors / compactions and pick hot-turn refs."""
    tool_calls = 0
    errors: list[int] = []
    compacts: list[int] = []
    seen_error_turns: set[int] = set()
    seen_compact_turns: set[int] = set()
    for e in events:
        etype = e.get("type")
        ti = e.get("turn_index")
        if not isinstance(ti, int):
            ti = e.get("spawned_in_turn")
        if etype == "tool_call":
            tool_calls += 1
        elif etype in ERROR_EVENT_TYPES or _subagent_failed(e):
            if isinstance(ti, int) and ti not in seen_error_turns:
                seen_error_turns.add(ti)
                errors.append(ti)
        elif etype in ("compact_complete", "compact_replace"):
            if isinstance(ti, int) and ti not in seen_compact_turns:
                seen_compact_turns.add(ti)
                compacts.append(ti)
    return {
        "tool_calls": tool_calls,
        "error_turns": sorted(errors),
        "compact_turns": sorted(compacts),
    }


def build_summary_payload(
    store: SessionStore, session_name: str, agent: str | None
) -> dict[str, Any]:
    """Aggregate stats for the Overview tab.

    When ``agent`` is omitted, sums across every agent listed in
    ``meta["agents"]``. Hot turns are the top-5 by cost; falls back to
    top-5 by total tokens when cost is unavailable for the provider.
    """
    meta = store.load_meta()
    agents = _agents_for_summary(meta, store, agent)

    if agent is None:
        flat_rollups = aggregate_turn_rollups(store)
    else:
        flat_rollups = rollups_or_derived(store, agents[0])

    totals = _aggregate_rollups(flat_rollups, count_by_agent=agent is None)

    if agent is None:
        # Channel-driven cycles lack turn indexes, so graph token and cost
        # totals use the turn-agnostic sum. Turn counts and hot turns retain
        # their positive-turn-index semantics.
        graph = graph_total_usage(store)
        totals["tokens"] = {
            "prompt": graph["tokens_in"],
            "completion": graph["tokens_out"],
            "cached": graph["tokens_cached"],
        }
        if graph["cost_usd"] is not None:
            totals["cost_usd"] = graph["cost_usd"]

    def _hot_key(r: dict) -> tuple[int, float]:
        c = r.get("cost_usd")
        if c is not None:
            try:
                return (0, float(c))
            except (TypeError, ValueError):
                pass
        return (1, float(r.get("tokens_in") or 0) + float(r.get("tokens_out") or 0))

    hot_sorted = sorted(flat_rollups, key=_hot_key, reverse=True)[:5]
    hot_turns = [
        {
            "agent": r.get("agent"),
            "turn_index": r.get("turn_index"),
            "cost_usd": r.get("cost_usd"),
            "tokens_in": r.get("tokens_in"),
            "tokens_out": r.get("tokens_out"),
        }
        for r in hot_sorted
    ]

    event_totals = {"tool_calls": 0, "error_turns": [], "compact_turns": []}
    for a in agents:
        per_agent = _scan_events_for_summary(store.get_events(a))
        event_totals["tool_calls"] += per_agent["tool_calls"]
        event_totals["error_turns"].extend(per_agent["error_turns"])
        event_totals["compact_turns"].extend(per_agent["compact_turns"])
    event_totals["error_turns"] = sorted(set(event_totals["error_turns"]))
    event_totals["compact_turns"] = sorted(set(event_totals["compact_turns"]))

    forks = len(meta.get("forked_children") or [])
    attached = len(store.discover_attached_agents())

    return {
        "session_name": session_name,
        "session_id": str(meta.get("session_id") or session_name),
        "format_version": meta.get("format_version"),
        "status": meta.get("status"),
        "created_at": meta.get("created_at"),
        "last_active": meta.get("last_active"),
        "config_type": meta.get("config_type"),
        "config_path": meta.get("config_path"),
        "agents": agents,
        "lineage": meta.get("lineage") or {},
        "totals": {
            **totals,
            "tool_calls": event_totals["tool_calls"],
            "errors": len(event_totals["error_turns"]),
            "compacts": len(event_totals["compact_turns"]),
            "forks": forks,
            "attached_agents": attached,
        },
        "hot_turns": hot_turns,
        "error_turns": event_totals["error_turns"],
        "compact_turns": event_totals["compact_turns"],
    }
