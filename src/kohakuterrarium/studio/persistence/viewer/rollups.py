"""Turn-rollup data fallback for the Session Viewer.

The viewer's Trace / Cost / Overview tabs read per-turn rows from the
``turn_rollup`` KVault table. Sessions that predate ``save_turn_rollup``
being wired into ``_handle_turn_token_usage`` (or any session whose
``turn_token_usage`` events haven't fired yet for the current turn)
have an empty rollup table — the tabs would otherwise look broken.

This module synthesises the same row shape from the events table so
the viewer always has data, even for archival sessions. It also folds
vertical sub-agent token usage (reported on parent ``subagent_result``
and live ``subagent_token_usage`` events) into the parent turn so
failed/interrupted sub-agents are reflected in trace/cost totals.
"""

from typing import Any

from kohakuterrarium.session.history import dedupe_adjacent_duplicate_events
from kohakuterrarium.session.store import SessionStore

# Same set used by the summary endpoint — kept in sync explicitly.
ERROR_EVENT_TYPES = frozenset({"tool_error", "subagent_error", "processing_error"})
_TOKEN_EVENT_TYPES = frozenset({"token_usage", "turn_token_usage"})
_SUBAGENT_TOKEN_EVENT_TYPES = frozenset({"subagent_token_usage", "subagent_result"})
_FAILED_FINAL_STATES = frozenset({"error", "interrupted", "cancelled"})


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_turn_index(evt: dict) -> int | None:
    """Return the turn bucket for an event, if it has one."""
    for key in ("turn_index", "spawned_in_turn"):
        value = evt.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _empty_row(agent: str, turn_index: int) -> dict[str, Any]:
    return {
        "agent": agent,
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
    }


def _ensure_row(by_turn: dict[int, dict[str, Any]], agent: str, turn: int) -> dict:
    row = by_turn.get(turn)
    if row is None:
        row = _empty_row(agent, turn)
        by_turn[turn] = row
    return row


def _touch_time(row: dict, evt: dict) -> None:
    ts = evt.get("ts")
    if ts is None:
        return
    if row["started_at"] is None or ts < row["started_at"]:
        row["started_at"] = ts
    if row["ended_at"] is None or ts > row["ended_at"]:
        row["ended_at"] = ts


def _usage_from_event(evt: dict) -> dict[str, Any]:
    prompt = evt.get("prompt_tokens", evt.get("tokens_in"))
    completion = evt.get("completion_tokens", evt.get("tokens_out"))
    cached = evt.get("cached_tokens", evt.get("tokens_cached"))
    tokens_in = _as_int(prompt)
    tokens_out = _as_int(completion)
    total = _as_int(evt.get("total_tokens"))
    if total <= 0:
        total = tokens_in + tokens_out
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_cached": _as_int(cached),
        "total_tokens": total,
        "cost_usd": _as_float(evt.get("cost_usd")),
    }


def _usage_has_value(usage: dict[str, Any]) -> bool:
    return bool(
        usage.get("tokens_in")
        or usage.get("tokens_out")
        or usage.get("tokens_cached")
        or usage.get("total_tokens")
        or usage.get("cost_usd") is not None
    )


def _with_usage_fallback(evt: dict, fallback: dict | None) -> dict:
    if not fallback:
        return evt
    merged = dict(evt)
    for primary, alias in (
        ("prompt_tokens", "tokens_in"),
        ("completion_tokens", "tokens_out"),
        ("cached_tokens", "tokens_cached"),
        ("total_tokens", "total_tokens"),
    ):
        current = _as_int(merged.get(primary, merged.get(alias)))
        other = _as_int(fallback.get(primary, fallback.get(alias)))
        if current <= 0 and other > 0:
            merged[primary] = other
    if merged.get("cost_usd") is None and fallback.get("cost_usd") is not None:
        merged["cost_usd"] = fallback.get("cost_usd")
    return merged


def _add_usage(row: dict, usage: dict[str, Any]) -> None:
    row["tokens_in"] += _as_int(usage.get("tokens_in"))
    row["tokens_out"] += _as_int(usage.get("tokens_out"))
    row["tokens_cached"] += _as_int(usage.get("tokens_cached"))
    cost = usage.get("cost_usd")
    if cost is not None:
        row["cost_usd"] = float(row["cost_usd"] or 0) + float(cost)


def _is_subagent_token_event(evt: dict) -> bool:
    return evt.get("type") in _SUBAGENT_TOKEN_EVENT_TYPES


def _subagent_failed(evt: dict) -> bool:
    if evt.get("type") != "subagent_result":
        return False
    final_state = str(evt.get("final_state") or "").lower()
    return bool(
        evt.get("success") is False
        or evt.get("error")
        or evt.get("interrupted")
        or evt.get("cancelled")
        or final_state in _FAILED_FINAL_STATES
    )


def _add_usage_bucket(
    buckets: dict[int, dict[str, Any]], turn: int, usage: dict[str, Any]
) -> None:
    bucket = buckets.setdefault(
        turn,
        {
            "tokens_in": 0,
            "tokens_out": 0,
            "tokens_cached": 0,
            "cost_usd": None,
        },
    )
    _add_usage(bucket, usage)


def derive_own_turns_from_events(events: list[dict], agent: str) -> list[dict]:
    """Synthesise parent/creature-owned per-turn rows from raw events.

    ``turn_token_usage`` is the authoritative per-turn total when
    present. Older/in-flight sessions may only have per-LLM-call
    ``token_usage`` events, so those are used only for turns that lack a
    ``turn_token_usage`` event. Sub-agent result tokens are intentionally
    excluded here; callers that want the full user-visible turn total
    should use :func:`rollups_or_derived`.
    """
    by_turn: dict[int, dict[str, Any]] = {}
    token_usage: dict[int, dict[str, Any]] = {}
    turn_usage: dict[int, dict[str, Any]] = {}

    for evt in events:
        ti = _event_turn_index(evt)
        if ti is None:
            continue
        row = _ensure_row(by_turn, agent, ti)
        _touch_time(row, evt)
        etype = evt.get("type")
        if etype in _TOKEN_EVENT_TYPES:
            usage = _usage_from_event(evt)
            if etype == "turn_token_usage":
                _add_usage_bucket(turn_usage, ti, usage)
            else:
                _add_usage_bucket(token_usage, ti, usage)
        elif etype == "tool_call":
            row["tool_calls"] += 1
        elif etype in ERROR_EVENT_TYPES or _subagent_failed(evt):
            row["has_error"] = True
        elif etype in ("compact_complete", "compact_replace"):
            row["compacted"] = True

    for ti, row in by_turn.items():
        usage = turn_usage.get(ti) or token_usage.get(ti)
        if usage:
            _add_usage(row, usage)
    return [by_turn[k] for k in sorted(by_turn.keys())]


def _subagent_label(parent: str, name: str, index: int) -> str:
    return f"{parent}:subagent:{name}:{index}"


def _subagent_name_from_event(evt: dict) -> str:
    raw = evt.get("name") or evt.get("subagent") or evt.get("subagent_name")
    if raw:
        return str(raw)
    job_id = str(evt.get("job_id") or "")
    if job_id.startswith("agent_") and "_" in job_id[len("agent_") :]:
        return job_id[len("agent_") :].rsplit("_", 1)[0]
    return "subagent"


def derive_subagent_turns_from_events(events: list[dict], parent: str) -> list[dict]:
    """Return one contribution row per vertical sub-agent job.

    Sub-agents do not write their own event namespace in the session DB.
    Completed runs normally expose final token counters on the parent's
    ``subagent_result`` event. Interrupted runs may never emit that final
    result, so the session output also persists live
    ``subagent_token_usage`` snapshots. Collapse both event types by
    ``job_id`` and keep the latest snapshot so the parent turn is not
    double counted when both events exist.
    """
    pending_updates: dict[str, dict[str, Any]] = {}
    result_rows: list[dict[str, Any]] = []
    anonymous: list[dict[str, Any]] = []
    for evt in events:
        if not _is_subagent_token_event(evt):
            continue
        ti = _event_turn_index(evt)
        if ti is None:
            continue
        usage = _usage_from_event(evt)
        failed = _subagent_failed(evt)
        if not failed and not _usage_has_value(usage):
            continue
        etype = evt.get("type")
        if (
            etype == "subagent_result"
            and not failed
            and usage.get("total_tokens", 0) <= 0
            and usage.get("tokens_cached", 0) <= 0
        ):
            continue
        job_id = str(evt.get("job_id") or "")
        if not job_id:
            anonymous.append(evt)
            continue
        if etype == "subagent_result":
            result_rows.append(
                _with_usage_fallback(evt, pending_updates.pop(job_id, None))
            )
            continue
        previous = pending_updates.get(job_id)
        previous_usage = _usage_from_event(previous) if previous else {}
        if previous is None or usage.get("total_tokens", 0) >= previous_usage.get(
            "total_tokens", 0
        ):
            pending_updates[job_id] = evt

    rows: list[dict[str, Any]] = []
    events_to_rows = result_rows + list(pending_updates.values()) + anonymous
    per_name_counts: dict[str, int] = {}
    for evt in events_to_rows:
        ti = _event_turn_index(evt)
        if ti is None:
            continue
        usage = _usage_from_event(evt)
        failed = _subagent_failed(evt)
        name = _subagent_name_from_event(evt)
        job_id = str(evt.get("job_id") or "")
        counter_key = job_id or name
        row_index = per_name_counts.get(counter_key, 0)
        per_name_counts[counter_key] = row_index + 1
        row = _empty_row(_subagent_label(parent, name, row_index), ti)
        row.update(
            {
                "parent_agent": parent,
                "subagent_name": name,
                "job_id": job_id,
                "started_at": evt.get("ts"),
                "ended_at": evt.get("ts"),
                "has_error": failed,
                "tools_used": evt.get("tools_used", []),
            }
        )
        _add_usage(row, usage)
        rows.append(row)
    return rows


def _merge_subagents(
    parent_rows: list[dict], sub_rows: list[dict], agent: str
) -> list[dict]:
    by_turn: dict[int, dict[str, Any]] = {}
    for row in parent_rows:
        ti = row.get("turn_index")
        if isinstance(ti, int) and ti > 0:
            by_turn[ti] = dict(row)
    for sub in sub_rows:
        ti = sub.get("turn_index")
        if not isinstance(ti, int) or ti <= 0:
            continue
        row = by_turn.get(ti)
        if row is None:
            row = _empty_row(agent, ti)
            by_turn[ti] = row
        _add_usage(row, _usage_from_event_for_row(sub))
        sa = sub.get("started_at")
        ea = sub.get("ended_at")
        if sa is not None and (row.get("started_at") is None or sa < row["started_at"]):
            row["started_at"] = sa
        if ea is not None and (row.get("ended_at") is None or ea > row["ended_at"]):
            row["ended_at"] = ea
        if sub.get("has_error"):
            row["has_error"] = True
        row.setdefault("subagent_breakdown", []).append(
            {
                "agent": sub.get("agent"),
                "subagent_name": sub.get("subagent_name"),
                "job_id": sub.get("job_id", ""),
                "tokens_in": sub.get("tokens_in", 0),
                "tokens_out": sub.get("tokens_out", 0),
                "tokens_cached": sub.get("tokens_cached", 0),
                "cost_usd": sub.get("cost_usd"),
                "has_error": bool(sub.get("has_error")),
            }
        )
    return [by_turn[k] for k in sorted(by_turn.keys())]


def _usage_from_event_for_row(row: dict) -> dict[str, Any]:
    return {
        "tokens_in": _as_int(row.get("tokens_in")),
        "tokens_out": _as_int(row.get("tokens_out")),
        "tokens_cached": _as_int(row.get("tokens_cached")),
        "cost_usd": _as_float(row.get("cost_usd")),
    }


def derive_turns_from_events(events: list[dict], agent: str) -> list[dict]:
    """Synthesise per-turn rollup rows from raw events.

    Each turn aggregates parent/creature LLM usage plus vertical
    sub-agent result usage. This is the read-side fallback for archived
    sessions and for any turn whose rollup row has not been persisted.
    """
    own_rows = derive_own_turns_from_events(events, agent)
    sub_rows = derive_subagent_turns_from_events(events, agent)
    return _merge_subagents(own_rows, sub_rows, agent)


def _own_rollups_or_derived(store: SessionStore, agent: str) -> list[dict]:
    rows = store.list_turn_rollups(agent)
    if rows:
        return rows
    events = dedupe_adjacent_duplicate_events(store.get_events(agent))
    return derive_own_turns_from_events(events, agent)


def rollups_or_derived(store: SessionStore, agent: str) -> list[dict]:
    """Return full turn rows for ``agent`` including sub-agent tokens."""
    events = dedupe_adjacent_duplicate_events(store.get_events(agent))
    own_rows = store.list_turn_rollups(agent)
    if not own_rows:
        own_rows = derive_own_turns_from_events(events, agent)
    sub_rows = derive_subagent_turns_from_events(events, agent)
    return _merge_subagents(own_rows, sub_rows, agent)


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
    meta = store.load_meta()
    for name in meta.get("agents") or []:
        if isinstance(name, str) and name and name not in seen:
            out.append((name, "main"))
            seen.add(name)
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


def _iter_rollup_contributions(store: SessionStore, name: str, kind: str):
    events = dedupe_adjacent_duplicate_events(store.get_events(name))
    for row in _own_rollups_or_derived(store, name):
        yield name, kind, row
    for row in derive_subagent_turns_from_events(events, name):
        yield row.get("agent") or name, "subagent", row


def aggregate_turn_rollups(store: SessionStore) -> list[dict]:
    """Per-turn rows summed across every controller loop contribution.

    Drives the Cost tab's "all agents combined" view. The breakdown
    includes main/attached agents plus vertical sub-agent result rows so
    a failed/interrupted sub-agent's tokens are visible even though it
    does not own an event namespace.
    """
    by_turn: dict[int, dict] = {}
    for name, kind in list_agent_namespaces(store):
        for contrib_name, contrib_kind, row in _iter_rollup_contributions(
            store, name, kind
        ):
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
                    "agent": contrib_name,
                    "kind": contrib_kind,
                    "tokens_in": tin,
                    "tokens_out": tout,
                    "tokens_cached": tcached,
                    "cost_usd": cost,
                    "tool_calls": tcalls,
                }
            )
    return [by_turn[k] for k in sorted(by_turn.keys())]
