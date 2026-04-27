"""Read-only handlers for the Session Viewer / Trace Viewer (V1 wave).

Surfaces four endpoints under ``viewer_router`` (mounted by
``sessions.py`` so the routes live at ``/api/sessions/{name}/...``):

* ``GET /{name}/tree``    — fork lineage + attached-agent DAG
* ``GET /{name}/summary`` — overview-tab stats
* ``GET /{name}/turns``   — paginated turn-rollup list
* ``GET /{name}/events``  — events with filters + cursor pagination

Lives in its own module so ``api/routes/sessions.py`` stays under the
600-line soft cap. All handlers open the store read-only
(``close(update_status=False)``) so browsing never bumps
``last_active``.

Design: ``plans/session-system/viewer-design.md`` §5.
"""

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from kohakuterrarium.api.routes._session_diff import build_diff_payload
from kohakuterrarium.api.routes._session_export import build_export
from kohakuterrarium.api.routes._session_rollups import (
    aggregate_turn_rollups as _aggregate_turn_rollups,
    rollups_or_derived as _rollups_or_derived,
)
from kohakuterrarium.session.store import SessionStore

# ─────────────────────────────────────────────────────────────────────
# Tree
# ─────────────────────────────────────────────────────────────────────


def build_tree_payload(store: SessionStore, session_name: str) -> dict[str, Any]:
    """Return ``{nodes, edges}`` for the session-tree pane.

    One hop in each direction: parent (if forked) and direct
    forked-children. Attached agents are always included recursively
    because they live in this same store. Walking the full fork tree
    would require opening every child file, which we defer to client-
    side navigation (the user clicks a child node, the frontend calls
    ``/tree`` again on that session).
    """
    meta = store.load_meta()
    session_id = str(meta.get("session_id") or session_name)
    lineage = meta.get("lineage") or {}
    fork_meta = lineage.get("fork") if isinstance(lineage, dict) else None
    forked_children = meta.get("forked_children") or []
    attached = store.discover_attached_agents()

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Root node = the session being viewed.
    nodes.append(
        {
            "id": session_id,
            "type": "session",
            "label": session_name,
            "format_version": meta.get("format_version"),
            "status": meta.get("status"),
            "created_at": meta.get("created_at"),
            "last_active": meta.get("last_active"),
            "is_focus": True,
        }
    )

    # Parent stub — only id is reliable without opening the file.
    if isinstance(fork_meta, dict):
        parent_id = fork_meta.get("parent_session_id")
        fork_point = fork_meta.get("fork_point")
        if parent_id:
            nodes.append(
                {
                    "id": str(parent_id),
                    "type": "session",
                    "label": str(parent_id),
                    "is_parent_stub": True,
                }
            )
            edges.append(
                {
                    "from": str(parent_id),
                    "to": session_id,
                    "type": "fork",
                    "at": fork_point,
                }
            )

    # Direct forked children — metadata-only nodes, no file opens.
    for child in forked_children:
        if not isinstance(child, dict):
            continue
        child_id = child.get("session_id")
        if not child_id:
            continue
        nodes.append(
            {
                "id": str(child_id),
                "type": "session",
                "label": str(child_id),
                "fork_point": child.get("fork_point"),
                "fork_created_at": child.get("fork_created_at"),
                "is_child_stub": True,
            }
        )
        edges.append(
            {
                "from": session_id,
                "to": str(child_id),
                "type": "fork",
                "at": child.get("fork_point"),
            }
        )

    # Attached agents — full nodes (they share the store).
    for entry in attached:
        ns = entry.get("namespace")
        if not ns:
            continue
        nodes.append(
            {
                "id": ns,
                "type": "attached",
                "label": entry.get("role") or ns,
                "host": entry.get("host"),
                "role": entry.get("role"),
                "attach_seq": entry.get("attach_seq"),
            }
        )
        edges.append(
            {
                "from": entry.get("host") or session_id,
                "to": ns,
                "type": "attach",
            }
        )

    return {
        "session_name": session_name,
        "session_id": session_id,
        "nodes": nodes,
        "edges": edges,
    }


# ─────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────

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
        rows = _rollups_or_derived(store, a)
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


# ─────────────────────────────────────────────────────────────────────
# Turns
# ─────────────────────────────────────────────────────────────────────


def build_turns_payload(
    store: SessionStore,
    session_name: str,
    *,
    agent: str | None,
    from_turn: int | None,
    to_turn: int | None,
    limit: int,
    offset: int,
    aggregate: bool = False,
) -> dict[str, Any]:
    """Paginated rollup rows. Drives the trace timeline + collapsed list.

    When ``aggregate`` is true, sum across **every** agent in the
    session (main + attached) and include a per-agent ``breakdown``
    field in each row. ``agent`` is then ignored. Used by the Cost tab
    to show a unified per-turn view of the whole session.
    """
    meta = store.load_meta()

    if aggregate:
        rows = _aggregate_turn_rollups(store)
        agent_used = None
    else:
        if agent is None:
            all_agents = list(meta.get("agents") or [])
            if not all_agents:
                raise HTTPException(404, f"Session has no agents: {session_name}")
            agent = all_agents[0]
        elif agent not in (meta.get("agents") or []):
            raise HTTPException(404, f"Agent not found in session: {agent}")
        rows = _rollups_or_derived(store, agent)
        agent_used = agent

    if from_turn is not None:
        rows = [r for r in rows if int(r.get("turn_index", -1)) >= from_turn]
    if to_turn is not None:
        rows = [r for r in rows if int(r.get("turn_index", -1)) <= to_turn]
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "session_name": session_name,
        "agent": agent_used,
        "aggregate": aggregate,
        "turns": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "from_turn": from_turn,
        "to_turn": to_turn,
    }


# ─────────────────────────────────────────────────────────────────────
# Events (filtered + paginated)
# ─────────────────────────────────────────────────────────────────────


def _parse_type_filter(types: str | None) -> set[str] | None:
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

    type_set = _parse_type_filter(types)
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


# ─────────────────────────────────────────────────────────────────────
# Sub-router (mounted by ``sessions.py``)
# ─────────────────────────────────────────────────────────────────────


def open_readonly(path: Path) -> SessionStore:
    """Open a session for read-only browsing.

    Caller is responsible for ``close(update_status=False)`` so the
    session's ``last_active`` timestamp does not get bumped just by
    listing / viewing it.
    """
    return SessionStore(path)


def build_viewer_router(
    *,
    resolve_session_path: Callable[[str], Path | None],
    canonical_name: Callable[[Path], str],
) -> APIRouter:
    """Build the viewer sub-router.

    ``resolve_session_path`` and ``canonical_name`` are injected so the
    router stays decoupled from the ``_SESSION_DIR`` module-level state
    in ``sessions.py``. Tests pass tmp-dir-aware versions.
    """
    router = APIRouter()

    def _open_or_404(session_name: str) -> tuple[SessionStore, str]:
        path = resolve_session_path(session_name)
        if path is None:
            raise HTTPException(404, f"Session not found: {session_name}")
        return open_readonly(path), canonical_name(path)

    @router.get("/{session_name}/tree")
    async def get_session_tree(session_name: str) -> dict[str, Any]:
        store, canonical = _open_or_404(session_name)
        try:
            return build_tree_payload(store, canonical)
        finally:
            store.close(update_status=False)

    @router.get("/{session_name}/summary")
    async def get_session_summary(
        session_name: str, agent: str | None = None
    ) -> dict[str, Any]:
        store, canonical = _open_or_404(session_name)
        try:
            return build_summary_payload(store, canonical, agent)
        finally:
            store.close(update_status=False)

    @router.get("/{session_name}/turns")
    async def get_session_turns(
        session_name: str,
        agent: str | None = None,
        from_turn: int | None = None,
        to_turn: int | None = None,
        limit: int = 200,
        offset: int = 0,
        aggregate: bool = False,
    ) -> dict[str, Any]:
        store, canonical = _open_or_404(session_name)
        try:
            return build_turns_payload(
                store,
                canonical,
                agent=agent,
                from_turn=from_turn,
                to_turn=to_turn,
                limit=max(1, min(limit, 1000)),
                offset=max(0, offset),
                aggregate=aggregate,
            )
        finally:
            store.close(update_status=False)

    @router.get("/{session_name}/export")
    async def get_session_export(
        session_name: str,
        format: str = "md",
        agent: str | None = None,
    ) -> Response:
        """Stream a session transcript in ``md`` / ``html`` / ``jsonl``."""
        path = resolve_session_path(session_name)
        if path is None:
            raise HTTPException(404, f"Session not found: {session_name}")
        store = open_readonly(path)
        try:
            content_type, body = build_export(
                store, canonical_name(path), format.lower(), agent
            )
        finally:
            store.close(update_status=False)
        ext = "md" if format == "md" else format.lower()
        filename = f"{canonical_name(path)}.{ext}"
        return Response(
            content=body,
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/{session_name}/diff")
    async def get_session_diff(
        session_name: str,
        other: str,
        agent: str | None = None,
    ) -> dict[str, Any]:
        """Structured diff against another saved session."""
        a_path = resolve_session_path(session_name)
        if a_path is None:
            raise HTTPException(404, f"Session not found: {session_name}")
        b_path = resolve_session_path(other)
        if b_path is None:
            raise HTTPException(404, f"Other session not found: {other}")
        return build_diff_payload(a_path, b_path, agent=agent)

    @router.get("/{session_name}/events")
    async def get_session_events(
        session_name: str,
        agent: str | None = None,
        turn_index: int | None = None,
        types: str | None = None,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 200,
        cursor: int | None = None,
    ) -> dict[str, Any]:
        store, canonical = _open_or_404(session_name)
        try:
            return build_events_payload(
                store,
                canonical,
                agent=agent,
                turn_index=turn_index,
                types=types,
                from_ts=from_ts,
                to_ts=to_ts,
                limit=max(1, min(limit, 1000)),
                cursor=cursor,
            )
        finally:
            store.close(update_status=False)

    return router
