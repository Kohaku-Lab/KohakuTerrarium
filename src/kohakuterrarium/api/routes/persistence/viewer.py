"""Persistence viewer — tree / summary / turns / events / diff / export.

Read-only Session Viewer endpoints. Paths use
``/{session_name}/<noun>`` so mounting the router under ``/api/sessions``
preserves the public URLs.

Handlers close saved stores with ``update_status=False`` so browsing never
changes ``last_active``. Payload builders perform synchronous SQLite and
filesystem work, so saved-store open, build, and close operations run as one
``asyncio.to_thread`` unit. Live sessions instead reuse the engine's attached
store on the event loop because a second same-file SQLite connection is not
reliable while the store is being written.

In multi-node mode, each cluster member writes to a per-worker store mirrored
at ``<session_dir>/mirror/<member_sid>.kohakutr``. Viewer routes resolve every
member store and merge payloads according to each endpoint's response shape;
reading only the primary mirror would omit peer activity. Standalone sessions
retain the single-store path so their reads avoid unnecessary fan-out.
"""

import asyncio
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.persistence.live_paths import (
    live_store_for,
    live_store_path,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.studio.persistence.viewer.diff import build_diff_payload
from kohakuterrarium.studio.persistence.viewer.events import build_events_payload
from kohakuterrarium.studio.persistence.viewer.export import build_export
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem
from kohakuterrarium.studio.persistence.viewer.summary import build_summary_payload
from kohakuterrarium.studio.persistence.viewer.timeline import build_timeline_payload
from kohakuterrarium.studio.persistence.viewer.tree import build_tree_payload
from kohakuterrarium.studio.persistence.viewer.turns import build_turns_payload
from kohakuterrarium.studio.sessions import cluster_fold
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


async def _resolve_or_404(session_name: str, service: TerrariumService | None = None):
    """Resolve a session path off-loop or raise 404 when it is unavailable.

    Live graph IDs resolve through the attached store because their on-disk
    filenames use creature IDs and cannot be found by graph-ID lookup.
    """
    if service is not None:
        live = live_store_path(service, session_name)
        if live is not None:
            return live
    path = await asyncio.to_thread(resolve_session_path_default, session_name)
    if path is None:
        raise HTTPException(404, f"Session not found: {session_name}")
    return path


def _resolve_cluster_paths(
    session_name: str, service: TerrariumService
) -> list[tuple[str, Path]]:
    """Resolve the available store paths for a standalone or clustered session.

    The module-level ``resolve_session_path_default`` binding is intentional:
    callers may replace this route-local resolution seam independently of the
    Studio helper. Standalone sessions produce one entry, while an unknown
    session produces none. Cluster members without a materialized mirror are
    omitted so available members remain viewable.

    Live graph IDs resolve through the engine's attached store before the
    on-disk fallback because their files are named by creature ID.
    """
    live = live_store_path(service, session_name)
    if live is not None:
        return [(session_name, live)]
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
    """Resolve cluster paths off-loop or raise 404 when no member is available."""
    members = await asyncio.to_thread(_resolve_cluster_paths, session_name, service)
    if not members:
        raise HTTPException(404, f"Session not found: {session_name}")
    return members


def _run_with_store(path, builder: Callable[[SessionStore, str], Any]) -> Any:
    """Open, read, and close a saved store as one calling-thread operation.

    Keeping the complete SQLite lifecycle together lets callers move the unit
    off the event loop without transferring a connection between threads.
    """
    store = SessionStore(path)
    try:
        return builder(store, normalize_session_stem(path))
    finally:
        store.close(update_status=False)


async def _build_single(
    service: TerrariumService,
    session_name: str,
    path: Path,
    builder: Callable[[SessionStore, str], Any],
) -> Any:
    """Build one session payload, reusing an attached live store when possible.

    Opening a second connection to an actively written store can raise
    ``SQLITE_IOERR`` on POSIX. Live reads therefore use the engine-owned store
    on the event loop, serialized with its writer, while saved-store reads run
    as an off-loop open/build/close unit.
    """
    store = live_store_for(service, session_name)
    if store is not None and str(getattr(store, "_path", "")) == str(path):
        return builder(store, normalize_session_stem(path))
    return await asyncio.to_thread(_run_with_store, path, builder)


def _run_per_member(
    members: list[tuple[str, Path]],
    builder: Callable[[SessionStore, str], Any],
) -> list[tuple[str, Any]]:
    """Build payloads from available member stores in input order.

    A corrupt mirror, incompatible schema, or member-local missing agent must
    not make the entire cluster view fail. Such members are omitted because a
    requested agent may legitimately exist in only part of the cluster.
    """
    out: list[tuple[str, Any]] = []
    for member_sid, path in members:
        try:
            payload = _run_with_store(path, builder)
        except Exception:  # noqa: BLE001 - member isolation is required
            continue
        out.append((member_sid, payload))
    return out


def _merge_tree(
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


def _merge_summary(
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


def _merge_turns(
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


def _merge_timeline(
    per_member: list[tuple[str, dict[str, Any]]],
    session_name: str,
    *,
    limit: int,
) -> dict[str, Any]:
    """Merge cluster timeline spans into one chronological sequence.

    Span IDs are monotonic only within one store, so member identity is part
    of the dedupe key and the timestamp tie-breaker, mirroring
    ``_merge_events``. When the combined sequence exceeds ``limit`` the
    latest spans are kept, matching single-member truncation.
    """
    spans: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for member_sid, payload in per_member:
        for span in payload.get("spans") or []:
            eid = span.get("eid")
            key = (member_sid, int(eid) if isinstance(eid, int) else -1)
            if key in seen:
                continue
            seen.add(key)
            tagged = dict(span)
            tagged.setdefault("member_sid", member_sid)
            spans.append(tagged)
    spans.sort(
        key=lambda s: (
            float(s.get("ts") or 0.0),
            str(s.get("member_sid") or ""),
            int(s.get("eid") or 0),
        )
    )
    truncated = len(spans) > limit
    if truncated:
        spans = spans[-limit:]
    return {
        "session_name": session_name,
        "agent": None,
        "spans": spans,
        "count": len(spans),
        "limit": limit,
        "truncated": truncated,
    }


@router.get("/{session_name}/tree")
async def get_session_tree(
    session_name: str,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    members = await _resolve_cluster_or_404(session_name, service)
    if len(members) == 1:
        # A standalone session needs no cross-store merge.
        return await _build_single(
            service, members[0][0], members[0][1], build_tree_payload
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
        return await _build_single(service, members[0][0], members[0][1], _build)
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
    # Cluster members have independent agent lists. Aggregate member payloads
    # to avoid rejecting members that do not contain the requested agent; the
    # outer merge restores one cross-cluster window.
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
        return await _build_single(service, members[0][0], members[0][1], _build)
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
    """Stream a session transcript in ``md``, ``html``, or ``jsonl``.

    Cluster exports contain only the first resolved member. Combining members
    requires format-specific framing rather than raw concatenation.
    """
    members = await _resolve_cluster_or_404(session_name, service)
    path = members[0][1]

    def _build(store: SessionStore, canonical: str) -> tuple[str, bytes | str]:
        return build_export(store, canonical, format.lower(), agent)

    content_type, body = await _build_single(service, members[0][0], path, _build)
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
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Return a structured diff against another saved session.

    Cluster sessions compare only their first resolved member because a
    multi-member diff requires an explicit member-pair selection policy.
    """
    a_store = live_store_for(service, session_name)
    a_path = await _resolve_or_404(session_name, service)
    b_store = live_store_for(service, other)
    if b_store is not None:
        b_path = Path(getattr(b_store, "_path"))
    else:
        b_path = await asyncio.to_thread(resolve_session_path_default, other)
        if b_path is None:
            raise HTTPException(404, f"Other session not found: {other}")
    if a_store is not None or b_store is not None:
        # Engine-owned stores must remain on their event-loop thread.
        return build_diff_payload(
            a_path, b_path, agent=agent, a_store=a_store, b_store=b_store
        )
    return await asyncio.to_thread(build_diff_payload, a_path, b_path, agent=agent)


@router.get("/{session_name}/timeline")
async def get_session_timeline(
    session_name: str,
    agent: str | None = None,
    limit: int = 20000,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Compact per-event timing spans for the trace-tab lane overview."""
    members = await _resolve_cluster_or_404(session_name, service)
    clamped_limit = max(1, min(limit, 50000))

    def _build(store: SessionStore, canonical: str) -> dict[str, Any]:
        return build_timeline_payload(
            store, canonical, agent=agent, limit=clamped_limit
        )

    if len(members) == 1:
        return await _build_single(service, members[0][0], members[0][1], _build)
    per_member = await asyncio.to_thread(_run_per_member, members, _build)
    return _merge_timeline(per_member, session_name, limit=clamped_limit)


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
        return await _build_single(service, members[0][0], members[0][1], _build)

    # Agent defaults are member-local, so one member may reject an agent that
    # is valid elsewhere. Member isolation keeps the remaining cluster view
    # available.
    per_member = await asyncio.to_thread(_run_per_member, members, _build)
    return _merge_events(per_member, session_name, limit=clamped_limit)
