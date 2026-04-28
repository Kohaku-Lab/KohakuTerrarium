"""Turn-rollup data fallback for the Session Viewer.

The viewer's Trace / Cost / Overview tabs read per-turn rows from the
``turn_rollup`` KVault table. Sessions that predate ``save_turn_rollup``
being wired into ``_handle_turn_token_usage`` (or any session whose
``turn_token_usage`` events haven't fired yet for the current turn)
have an empty rollup table — the tabs would otherwise look broken.

This module synthesises the same row shape from the events table so
the viewer always has data, even for archival sessions.
"""

from typing import Any

from kohakuterrarium.session.store import SessionStore

# Same set used by the summary endpoint — kept in sync explicitly.
ERROR_EVENT_TYPES = frozenset({"tool_error", "subagent_error", "processing_error"})


def derive_turns_from_events(events: list[dict], agent: str) -> list[dict]:
    """Synthesise per-turn rollup rows from raw events.

    Each turn aggregates:

    * ``started_at`` / ``ended_at`` from the first / last event ts
    * ``tokens_in`` / ``tokens_out`` / ``tokens_cached`` from
      ``turn_token_usage`` (preferred) or ``token_usage`` events
    * ``tool_calls`` count
    * ``has_error`` and ``compacted`` flags
    """
    by_turn: dict[int, dict[str, Any]] = {}
    for evt in events:
        ti = evt.get("turn_index")
        if not isinstance(ti, int) or ti <= 0:
            continue
        row = by_turn.get(ti)
        if row is None:
            row = {
                "agent": agent,
                "turn_index": ti,
                "started_at": None,
                "ended_at": None,
                "tokens_in": 0,
                "tokens_out": 0,
                "tokens_cached": 0,
                "cost_usd": None,
                "tool_calls": 0,
                "has_error": False,
                "compacted": False,
            }
            by_turn[ti] = row
        ts = evt.get("ts")
        if ts is not None:
            if row["started_at"] is None or ts < row["started_at"]:
                row["started_at"] = ts
            if row["ended_at"] is None or ts > row["ended_at"]:
                row["ended_at"] = ts
        etype = evt.get("type")
        if etype == "turn_token_usage" or etype == "token_usage":
            row["tokens_in"] += int(evt.get("prompt_tokens") or 0)
            row["tokens_out"] += int(evt.get("completion_tokens") or 0)
            row["tokens_cached"] += int(evt.get("cached_tokens") or 0)
            cost = evt.get("cost_usd")
            if cost is not None:
                try:
                    row["cost_usd"] = float(row["cost_usd"] or 0) + float(cost)
                except (TypeError, ValueError):
                    pass
        elif etype == "tool_call":
            row["tool_calls"] += 1
        elif etype in ERROR_EVENT_TYPES:
            row["has_error"] = True
        elif etype in ("compact_complete", "compact_replace"):
            row["compacted"] = True
    return [by_turn[k] for k in sorted(by_turn.keys())]


def rollups_or_derived(store: SessionStore, agent: str) -> list[dict]:
    """Return rollup rows for ``agent``, deriving from events if the
    table is empty."""
    rows = store.list_turn_rollups(agent)
    if rows:
        return rows
    return derive_turns_from_events(store.get_events(agent), agent)


def list_agent_namespaces(store: SessionStore) -> list[tuple[str, str]]:
    """Return ``[(agent_name, kind), ...]`` for every agent that has
    written into this store.

    ``kind`` is ``"main"`` for top-level agents (those listed in
    ``meta.agents`` plus any later-discovered ones), and
    ``"attached"`` for namespaces under
    ``<host>:attached:<role>:<seq>`` produced by Wave F attach.
    The order matches first-seen-in-events so the breakdown lists
    stay deterministic across requests.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in store.discover_agents_from_events():
        if name not in seen:
            out.append((name, "main"))
            seen.add(name)
    for entry in store.discover_attached_agents():
        ns = entry.get("namespace") or ""
        if ns and ns not in seen:
            out.append((ns, "attached"))
            seen.add(ns)
    return out


def _empty_aggregate(turn_index: int) -> dict:
    return {
        "turn_index": turn_index,
        "started_at": None,
        "ended_at": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_cached": 0,
        "cost_usd": None,
        "tool_calls": 0,
        "has_error": False,
        "compacted": False,
        "breakdown": [],
    }


def aggregate_turn_rollups(store: SessionStore) -> list[dict]:
    """Per-turn rows summed across **every** agent in the session.

    Drives the Cost tab's "all agents combined" view, where each turn
    needs both the aggregate token count AND a breakdown of which
    agent contributed how much. Sub-agents and attached agents share
    ``turn_index`` with their parent's turn (via the framework's
    ``spawned_in_turn`` stamping) so summing across all namespaces by
    ``turn_index`` is the right grouping.
    """
    by_turn: dict[int, dict] = {}
    for name, kind in list_agent_namespaces(store):
        for row in rollups_or_derived(store, name):
            ti = row.get("turn_index")
            if not isinstance(ti, int) or ti <= 0:
                continue
            agg = by_turn.get(ti)
            if agg is None:
                agg = _empty_aggregate(ti)
                by_turn[ti] = agg
            tin = int(row.get("tokens_in") or 0)
            tout = int(row.get("tokens_out") or 0)
            tcached = int(row.get("tokens_cached") or 0)
            tcalls = int(row.get("tool_calls") or 0)
            agg["tokens_in"] += tin
            agg["tokens_out"] += tout
            agg["tokens_cached"] += tcached
            agg["tool_calls"] += tcalls
            cost = row.get("cost_usd")
            if cost is not None:
                try:
                    agg["cost_usd"] = float(agg["cost_usd"] or 0) + float(cost)
                except (TypeError, ValueError):
                    pass
            sa = row.get("started_at")
            ea = row.get("ended_at")
            if sa is not None and (agg["started_at"] is None or sa < agg["started_at"]):
                agg["started_at"] = sa
            if ea is not None and (agg["ended_at"] is None or ea > agg["ended_at"]):
                agg["ended_at"] = ea
            if row.get("has_error"):
                agg["has_error"] = True
            if row.get("compacted"):
                agg["compacted"] = True
            agg["breakdown"].append(
                {
                    "agent": name,
                    "kind": kind,
                    "tokens_in": tin,
                    "tokens_out": tout,
                    "tokens_cached": tcached,
                    "cost_usd": cost,
                    "tool_calls": tcalls,
                }
            )
    return [by_turn[k] for k in sorted(by_turn.keys())]
