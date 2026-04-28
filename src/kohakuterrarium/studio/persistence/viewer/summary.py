"""Overview-tab summary builder for the Session Viewer.

Verbatim port of ``_session_viewer.py:build_summary_payload`` plus the
private helpers it uses (``_aggregate_rollups``,
``_scan_events_for_summary``, ``_agents_for_summary``).
"""

from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.rollups import rollups_or_derived

# Event types that count as "errors" for the overview tab. Kept narrow
# so a turn with one tool retry-and-success doesn't show as broken.
_ERROR_EVENT_TYPES = frozenset({"tool_error", "subagent_error", "processing_error"})


def _agents_for_summary(meta: dict[str, Any], requested: str | None) -> list[str]:
    """Return the agent list to summarise.

    ``requested`` narrows to one agent (404 if not present); ``None``
    summarises every agent in ``meta["agents"]``.
    """
    all_agents = list(meta.get("agents") or [])
    if requested is None:
        return all_agents
    if requested not in all_agents:
        raise HTTPException(404, f"Agent not found in session: {requested}")
    return [requested]


def _aggregate_rollups(rollups: Iterable[dict]) -> dict[str, Any]:
    """Sum a sequence of turn-rollup rows into one totals dict."""
    prompt = completion = cached = 0
    cost_usd = 0.0
    cost_seen = False
    turns = 0
    for r in rollups:
        turns += 1
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
        if etype == "tool_call":
            tool_calls += 1
        elif etype in _ERROR_EVENT_TYPES:
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
    agents = _agents_for_summary(meta, agent)

    # Per-agent rollups, then a flat list for hot-turn selection.
    # Falls back to events-derived rows when the rollup table is empty
    # (which is the case for any session whose ``turn_token_usage``
    # writer hasn't been wired through ``save_turn_rollup`` yet — see
    # ``session/output.py``).
    rollups_by_agent: dict[str, list[dict]] = {}
    flat_rollups: list[dict] = []
    for a in agents:
        rows = rollups_or_derived(store, a)
        rollups_by_agent[a] = rows
        flat_rollups.extend(rows)

    totals = _aggregate_rollups(flat_rollups)

    # Hot turns — by cost when available, else by token volume.
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

    # Event-derived counters: tool_calls, errors, compacts.
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
