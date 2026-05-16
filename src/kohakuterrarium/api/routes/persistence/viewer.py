"""Persistence viewer — tree / summary / turns / events / diff / export.

Read-only endpoints for the Session Viewer (V1+V6 waves). Paths are
``/{session_name}/<noun>`` so the router can be mounted under
``/api/sessions`` for URL preservation.

All handlers open the store read-only (``close(update_status=False)``)
so browsing never bumps ``last_active``. Every payload builder is
sync (SQLite + filesystem), so each route dispatches the open +
build + close sequence to a worker thread via ``asyncio.to_thread`` —
the event loop stays free for concurrent API traffic.

CF-5 follow-up — cluster fan-out. In multi-node mode each cluster
member writes events to its OWN per-worker store, mirrored host-side
as ``<session_dir>/mirror/<member_sid>.kohakutr``. Opening only the
primary's mirror surfaces a one-sided view (memory search hit this
in CF-5; the viewer routes share the blind spot). The viewer routes
resolve the requested sid via :func:`_resolve_cluster_paths` (same
helper memory search uses post-CF-5), fan out across every member's
store, and merge per the endpoint's payload shape. Standalone mode
returns a single-entry list — the routes take the existing scalar
fast path (one ``_run_with_store`` call) so unit tests that stub it
keep working.
"""

import asyncio
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.studio.persistence.viewer.diff import build_diff_payload
from kohakuterrarium.studio.persistence.viewer.events import build_events_payload
from kohakuterrarium.studio.persistence.viewer.export import build_export
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem
from kohakuterrarium.studio.persistence.viewer.summary import build_summary_payload
from kohakuterrarium.studio.persistence.viewer.tree import build_tree_payload
from kohakuterrarium.studio.persistence.viewer.turns import build_turns_payload
from kohakuterrarium.studio.sessions import cluster_fold
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


async def _resolve_or_404(session_name: str):
    """Resolve a session path off-loop; raise 404 if missing."""
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return path


def _resolve_cluster_paths(
    session_name: str, service: TerrariumService
) -> list[tuple[str, Path]]:
    """Cluster-aware on-disk path resolution for the viewer routes.

    Mirrors :func:`studio.sessions.cluster_paths.resolve_cluster_member_paths`
    but uses the *module-level* ``resolve_session_path_default`` binding
    so unit tests can still monkeypatch ``viewer_mod.resolve_session_path_default``
    and have it affect the route's path lookup.

    Standalone mode (no cluster links): returns a single-entry list with
    ``session_name``'s own resolved path — routes take the existing
    scalar fast path. Empty list means the sid is unknown on disk and
    the caller raises 404. Members whose mirror has not yet
    materialised are silently skipped (matching memory search).
    """
    primary = cluster_fold.sid_to_primary(service).get(session_name, session_name)
    members = cluster_fold.cluster_groups(service).get(primary, {session_name})
    out: list[tuple[str, Path]] = []
    for member_sid in sorted(members):
        path = resolve_session_path_default(member_sid)
        if path is None:
            continue
        out.append((member_sid, path))
    return out


async def _resolve_cluster_or_404(
    session_name: str, service: TerrariumService
) -> list[tuple[str, Path]]:
    """Off-loop cluster path resolution; raise 404 if no member resolves."""
    members = await asyncio.to_thread(_resolve_cluster_paths, session_name, service)
    if not members:
        raise HTTPException(404, f"Session not found: {session_name}")
    return members


def _run_with_store(path, builder: Callable[[SessionStore, str], Any]) -> Any:
    """Open store, run builder, close — all on the calling thread.

    Designed to be wrapped in :func:`asyncio.to_thread` so the SQLite
    open + the payload build + the close happen as one off-loop unit.
    """
    store = SessionStore(path)
    try:
        return builder(store, normalize_session_stem(path))
    finally:
        store.close(update_status=False)


def _run_per_member(
    members: list[tuple[str, Path]],
    builder: Callable[[SessionStore, str], Any],
) -> list[tuple[str, Any]]:
    """Open each member's store in sequence, run builder, close.

    Returns ``[(member_sid, payload), ...]`` in the same order as
    ``members``. Failures on a single member (corrupted mirror, schema
    mismatch, or the agent default lookup raising 404) are swallowed
    and that member is omitted — without this, one bad worker mirror
    could 500 the whole cluster view, AND the per-member ``_build``
    closures call ``build_*_payload`` which itself raises
    ``HTTPException(404)`` when the requested agent is missing in
    *that* member's meta (perfectly normal for cluster fan-out).
    """
    out: list[tuple[str, Any]] = []
    for member_sid, path in members:
        try:
            payload = _run_with_store(path, builder)
        except Exception:  # noqa: BLE001 — see docstring
            continue
        out.append((member_sid, payload))
    return out


# ─────────────────────────────────────────────────────────────────────
# Cluster-aware merge helpers — one per endpoint's payload shape.
# ─────────────────────────────────────────────────────────────────────


def _merge_tree(
    per_member: list[tuple[str, dict[str, Any]]], session_name: str
) -> dict[str, Any]:
    """Union ``nodes`` + ``edges`` across members, deduplicated by id.

    The viewer's tree pane shows fork lineage + attached agents. In a
    cluster each member contributes its own attached-agent slice; fork
    lineage is per-member but usually disjoint (each worker forks its
    own sub-tree). De-dup nodes by ``id`` (first-write-wins, primary
    first via lex order) so a creature attached to multiple members
    doesn't appear twice. Edges de-dup by ``(from, to, type)``.
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


def _merge_summary(
    per_member: list[tuple[str, dict[str, Any]]], session_name: str
) -> dict[str, Any]:
    """Aggregate Overview-tab stats across cluster members.

    Numerical fields (turns, tokens, tool_calls, errors, compacts,
    forks, attached_agents) sum. Lists (``agents``, ``error_turns``,
    ``compact_turns``, ``hot_turns``) union; hot_turns truncates to 5
    by cost / token volume. Identity-ish fields (``created_at``,
    ``status``, ``config_path``, ``config_type``, ``format_version``)
    come from the primary's payload (first in sorted order).
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


def _merge_turns(
    per_member: list[tuple[str, dict[str, Any]]],
    session_name: str,
    *,
    limit: int,
    offset: int,
    from_turn: int | None,
    to_turn: int | None,
) -> dict[str, Any]:
    """Chronological merge of paginated turn rows across cluster members.

    Each turn row carries ``turn_index`` (per-agent) and ``agent`` (in
    aggregate mode) / inferred from member-sid. Sort by ``turn_index``
    asc then ``agent`` asc for stable ordering, then re-apply the
    requested ``offset``/``limit`` window over the merged total.
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
    total = len(rows)
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


def _merge_events(
    per_member: list[tuple[str, dict[str, Any]]],
    session_name: str,
    *,
    limit: int,
) -> dict[str, Any]:
    """Chronological merge of paginated event rows across cluster members.

    Each event row has ``event_id`` (per-store), ``ts`` (wall-clock),
    and ``type``. Sort by ``ts`` asc then by ``(member_sid, event_id)``
    for stability across members whose event_ids reset to 0
    independently. De-dup by ``(member_sid, event_id)`` — each member's
    store assigns its own monotonic event_id, so the tuple is unique
    even when two members happen to share a numeric id.
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


# ─────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────


@router.get("/{session_name}/tree")
async def get_session_tree(
    session_name: str,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    members = await _resolve_cluster_or_404(session_name, service)
    if len(members) == 1:
        # Standalone fast path — preserves single-store unit-test stubs.
        return await asyncio.to_thread(
            _run_with_store, members[0][1], build_tree_payload
        )
    per_member = await asyncio.to_thread(_run_per_member, members, build_tree_payload)
    return _merge_tree(per_member, session_name)


@router.get("/{session_name}/summary")
async def get_session_summary(
    session_name: str,
    agent: str | None = None,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    members = await _resolve_cluster_or_404(session_name, service)

    def _build(store: SessionStore, canonical: str) -> dict[str, Any]:
        return build_summary_payload(store, canonical, agent)

    if len(members) == 1:
        return await asyncio.to_thread(_run_with_store, members[0][1], _build)
    per_member = await asyncio.to_thread(_run_per_member, members, _build)
    return _merge_summary(per_member, session_name)


@router.get("/{session_name}/turns")
async def get_session_turns(
    session_name: str,
    agent: str | None = None,
    from_turn: int | None = None,
    to_turn: int | None = None,
    limit: int = 200,
    offset: int = 0,
    aggregate: bool = False,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    members = await _resolve_cluster_or_404(session_name, service)
    clamped_limit = max(1, min(limit, 1000))
    clamped_offset = max(0, offset)
    # Cluster fan-out forces per-member aggregation: each member has
    # its own agents and ``build_turns_payload`` with ``aggregate=False``
    # would 404 a member whose agent list doesn't include the requested
    # ``agent``. The outer ``_merge_turns`` unions the per-member
    # aggregate windows. Standalone keeps the caller's flag.
    fanout_aggregate = aggregate or len(members) > 1

    def _build(store: SessionStore, canonical: str) -> dict[str, Any]:
        return build_turns_payload(
            store,
            canonical,
            agent=agent,
            from_turn=from_turn,
            to_turn=to_turn,
            limit=clamped_limit,
            offset=clamped_offset,
            aggregate=fanout_aggregate,
        )

    if len(members) == 1:
        return await asyncio.to_thread(_run_with_store, members[0][1], _build)
    per_member = await asyncio.to_thread(_run_per_member, members, _build)
    return _merge_turns(
        per_member,
        session_name,
        limit=clamped_limit,
        offset=clamped_offset,
        from_turn=from_turn,
        to_turn=to_turn,
    )


@router.get("/{session_name}/export")
async def get_session_export(
    session_name: str,
    format: str = "md",
    agent: str | None = None,
    service: TerrariumService = Depends(get_service),
) -> Response:
    """Stream a session transcript in ``md`` / ``html`` / ``jsonl``.

    CF-5 deferred: cluster bundles. Today returns ONLY the primary
    member's transcript — multi-member export needs a per-format
    concat strategy (md headers per member, html details blocks,
    jsonl interleave).
    """
    members = await _resolve_cluster_or_404(session_name, service)
    path = members[0][1]

    def _build(store: SessionStore, canonical: str) -> tuple[str, bytes | str]:
        return build_export(store, canonical, format.lower(), agent)

    content_type, body = await asyncio.to_thread(_run_with_store, path, _build)
    ext = "md" if format == "md" else format.lower()
    filename = f"{normalize_session_stem(path)}.{ext}"
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
    """Structured diff against another saved session.

    CF-5 deferred: cluster diff. Today diffs ONLY the primary member's
    store; multi-member diff needs a per-pair strategy (likely
    per-member-pair diff with caller choosing).
    """
    a_path = await _resolve_or_404(session_name)
    b_path = await asyncio.to_thread(resolve_session_path_default, other)
    if b_path is None:
        raise HTTPException(404, f"Other session not found: {other}")
    return await asyncio.to_thread(build_diff_payload, a_path, b_path, agent=agent)


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
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    members = await _resolve_cluster_or_404(session_name, service)
    clamped_limit = max(1, min(limit, 1000))

    def _build(store: SessionStore, canonical: str) -> dict[str, Any]:
        return build_events_payload(
            store,
            canonical,
            agent=agent,
            turn_index=turn_index,
            types=types,
            from_ts=from_ts,
            to_ts=to_ts,
            limit=clamped_limit,
            cursor=cursor,
        )

    if len(members) == 1:
        return await asyncio.to_thread(_run_with_store, members[0][1], _build)

    # In cluster fan-out each member's payload is built for whatever
    # ``agent`` defaulting that member's meta picks. Errors from one
    # member (e.g. that member doesn't have ``agent``) drop that member
    # from the merge rather than 404-ing the whole request — the merge
    # over the others still surfaces a coherent cross-cluster view.
    per_member = await asyncio.to_thread(_run_per_member, members, _build)
    return _merge_events(per_member, session_name, limit=clamped_limit)
