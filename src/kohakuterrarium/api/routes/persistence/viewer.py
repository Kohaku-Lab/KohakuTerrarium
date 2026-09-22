"""Persistence viewer — tree / summary / turns / events / diff / export.

Read-only Session Viewer endpoints. Paths use
``/{session_name}/<noun>`` so mounting the router under ``/api/sessions``
preserves the public URLs.

Handlers close saved stores with ``update_status=False`` so browsing never
changes ``last_active``. Payload builders perform synchronous SQLite and
filesystem work, so saved-store open, build, and close operations run as one
``asyncio.to_thread`` unit. Live sessions instead reuse the engine's attached
store on its single-worker affinity thread because a second same-file SQLite
connection is not reliable while the store is being written.

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
from kohakuterrarium.api.routes.persistence.cluster_merge import (
    merge_events,
    merge_summary,
    merge_tree,
    merge_turns,
)
from kohakuterrarium.api.routes.persistence.live_paths import (
    live_store_for,
    live_store_path,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.studio.persistence.viewer.diff import (
    _load_messages,
    build_diff_payload,
    merge_diff_payload,
)
from kohakuterrarium.studio.persistence.viewer.events import build_events_payload
from kohakuterrarium.studio.persistence.viewer.export import build_export
from kohakuterrarium.studio.persistence.viewer.paths import normalize_session_stem
from kohakuterrarium.studio.persistence.viewer.summary import build_summary_payload
from kohakuterrarium.studio.persistence.viewer.timeline import (
    build_timeline_payload,
    merge_timeline_payloads,
)
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
    ``SQLITE_IOERR`` on POSIX. Live reads reuse the engine-owned store on its
    affinity thread, while saved-store reads run as an off-loop open/build/close
    unit.
    """
    store = live_store_for(service, session_name)
    if store is not None and str(getattr(store, "_path", "")) == str(path):
        name = normalize_session_stem(path)
        return await store.run(builder, store, name)
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
    return merge_tree(per_member, session_name)


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
    return merge_summary(per_member, session_name)


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

    # Cluster pagination is applied after member rows are merged. Each member
    # must therefore contribute enough of its prefix to cover the requested
    # merged window; applying the offset here as well would skip rows twice.
    member_offset = clamped_offset if len(members) == 1 else 0
    member_limit = (
        clamped_limit if len(members) == 1 else clamped_offset + clamped_limit
    )

    def _build(store: SessionStore, canonical: str) -> dict[str, Any]:
        return build_turns_payload(
            store,
            canonical,
            agent=agent,
            from_turn=from_turn,
            to_turn=to_turn,
            limit=member_limit,
            offset=member_offset,
            aggregate=fanout_aggregate,
        )

    if len(members) == 1:
        return await _build_single(service, members[0][0], members[0][1], _build)
    per_member = await asyncio.to_thread(_run_per_member, members, _build)
    return merge_turns(
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
    if a_store is None and b_store is None:
        return await asyncio.to_thread(build_diff_payload, a_path, b_path, agent=agent)

    # Each live store loads its own side on its affinity thread; a saved side
    # opens and closes its store as one worker-thread unit. Loads run
    # concurrently and the merge is pure in-memory work on the event loop.
    # No annotations here: nested-def annotations evaluate on every call on
    # Python < 3.14, and tests monkeypatch SessionStore with plain functions.

    async def _side(store, path):
        if store is not None:
            return await store.run(_load_messages, path, agent, store)
        return await asyncio.to_thread(_load_messages, path, agent, None)

    a_side, b_side = await asyncio.gather(
        _side(a_store, a_path), _side(b_store, b_path)
    )
    return merge_diff_payload(a_side, b_side)


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
    return merge_timeline_payloads(per_member, session_name, limit=clamped_limit)


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
    return merge_events(per_member, session_name, limit=clamped_limit)
