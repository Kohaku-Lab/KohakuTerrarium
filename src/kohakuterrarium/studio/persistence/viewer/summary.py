"""Overview-tab summary builder for the Session Viewer.

Builds overview totals, hot turns, and event-derived counters for persisted
sessions.
"""

from collections.abc import Iterable
from typing import Any

from kohakuterrarium.session.history.replay import dedupe_adjacent_duplicate_events
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.viewer.agents import (
    viewer_agent_names as _agents_for_summary,
)
from kohakuterrarium.studio.persistence.viewer.rollups import (
    _add_usage,
    _aggregate_rollup_contributions,
    _creature_total_usage,
    _event_failed,
    _iter_rollup_contributions_from_events,
    _merge_subagents,
    _own_rollups_from_events,
    derive_subagent_turns_from_events,
    list_agent_namespaces,
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
        elif _event_failed(e):
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


def _namespace_summary_stats(
    store: SessionStore, name: str, kind: str, *, for_graph: bool
) -> tuple[list[Any], dict[str, Any] | None, dict[str, Any]]:
    """Return derived rollups, usage, and raw diagnostics for one namespace."""
    raw = store.get_events(name)
    diagnostics = _scan_events_for_summary(raw)
    events = dedupe_adjacent_duplicate_events(raw)
    stored = store.list_turn_rollups(name)
    if for_graph:
        rows: list[Any] = list(
            _iter_rollup_contributions_from_events(name, kind, events, stored)
        )
        usage: dict[str, Any] | None = _creature_total_usage(events, stored)
    else:
        own_rows = _own_rollups_from_events(name, events, stored)
        sub_rows = derive_subagent_turns_from_events(events, name)
        rows = _merge_subagents(own_rows, sub_rows, name)
        usage = None
    return rows, usage, diagnostics


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
    names = list_agent_namespaces(store) if agent is None else [(agents[0], "main")]
    contributions: dict[str, list[Any]] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    graph = {"tokens_in": 0, "tokens_out": 0, "tokens_cached": 0, "cost_usd": None}
    for name, kind in names:
        rows, usage, diag = _namespace_summary_stats(
            store, name, kind, for_graph=agent is None
        )
        contributions[name] = rows
        diagnostics[name] = diag
        if usage is not None:
            _add_usage(graph, usage)

    if agent is None:
        flat_rollups = _aggregate_rollup_contributions(
            contrib for name, _kind in names for contrib in contributions[name]
        )
    else:
        flat_rollups = contributions[agents[0]]

    totals = _aggregate_rollups(flat_rollups, count_by_agent=agent is None)

    if agent is None:
        # Channel-driven cycles lack turn indexes, so graph token and cost
        # totals use the turn-agnostic sum. Turn counts and hot turns retain
        # their positive-turn-index semantics.
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

    event_totals: dict[str, Any] = {
        "tool_calls": 0,
        "error_turns": [],
        "compact_turns": [],
    }
    for a in agents:
        namespace = str(a)
        if namespace not in diagnostics:
            diagnostics[namespace] = _scan_events_for_summary(
                store.get_events(namespace)
            )
        per_agent = diagnostics[namespace]
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
