"""Sessions memory — FTS5 / vector / hybrid search over a saved session.

Path is ``/{session_name}/memory/search`` so the router can be mounted
under ``/api/sessions`` for URL preservation: the frontend's
``sessionAPI.searchSession`` calls ``GET
/sessions/{name}/memory/search``.
"""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.studio._runtime import host_engine_or_none
from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.studio.sessions import cluster_fold
from kohakuterrarium.studio.sessions.memory_search import search_session_memory
from kohakuterrarium.terrarium.service import TerrariumService

router = APIRouter()


def _resolve_cluster_member_paths(
    session_sid: str, service: TerrariumService
) -> list[tuple[str, Path]]:
    """Return ``[(member_sid, path)]`` for every reachable cluster member.

    Mirrors :func:`studio.sessions.cluster_paths.resolve_cluster_member_paths`
    but uses the module-level ``resolve_session_path_default`` binding
    so unit tests can monkeypatch ``memory_mod.resolve_session_path_default``
    and have it affect the route's path lookup.

    See ``studio/sessions/cluster_paths.py`` for the canonical
    docstring — short version: maps the requested sid to its cluster
    primary, walks every member sid, resolves each member's mirror
    path, skips members whose mirror hasn't materialised. Standalone
    mode (no cluster links) returns a single-entry list with the
    requested sid's own path so the caller takes the existing scalar
    fast path.
    """
    primary = cluster_fold.sid_to_primary(service).get(session_sid, session_sid)
    members = cluster_fold.cluster_groups(service).get(primary, {session_sid})
    out: list[tuple[str, Path]] = []
    for member_sid in sorted(members):
        path = resolve_session_path_default(member_sid)
        if path is None:
            continue
        out.append((member_sid, path))
    return out


def _merge_member_results(
    member_results: list[dict[str, Any]], k: int
) -> list[dict[str, Any]]:
    """Merge per-member search payloads into one ``results`` list.

    Each payload has shape ``{"results": [{"score": float, ...}, ...]}``.
    Sort the union by descending ``score`` (FTS BM25 / vector similarity
    / RRF fusion all expose a comparable ``score`` float) and trim to
    ``k``. Hits with no ``score`` are sorted to the end so a partial
    failure on one member can't crowd out a member with real scores.
    """
    merged: list[dict[str, Any]] = []
    for payload in member_results:
        merged.extend(payload.get("results") or [])
    merged.sort(
        key=lambda r: (
            r.get("score", 0.0) if isinstance(r.get("score"), (int, float)) else 0.0
        ),
        reverse=True,
    )
    return merged[:k]


@router.get("/{session_name}/memory/search")
async def search_session_memory_route(
    session_name: str,
    q: str,
    mode: str = "auto",
    k: int = 10,
    agent: str | None = None,
    service: TerrariumService = Depends(get_service),
) -> dict[str, Any]:
    """Search a session's memory via FTS5 or semantic / hybrid modes.

    Read-only. Wraps the existing ``SessionMemory.search()`` — no new
    indexing behavior. Modes: ``auto`` (default), ``fts``, ``semantic``,
    ``hybrid``.

    CF-5: in multi-node mode the requested ``session_name`` is usually a
    cluster's primary sid. Each cluster member writes events to its OWN
    per-worker store (mirrored host-side as
    ``<session_dir>/mirror/<member_sid>.kohakutr``); opening only the
    primary's mirror missed every hit from the other members. Resolve
    the cluster via :func:`cluster_fold.cluster_groups`, search each
    member's mirror, and merge by score. Standalone falls through with
    a single member (the requested sid itself) — same behaviour as
    before.

    Service-routed: in lab-host mode the host runs no agent engine, so
    the "find a live creature with this session path" optimisation has
    no host-side hit to begin with — ``host_engine_or_none`` returns
    ``None`` and the search opens a fresh :class:`SessionStore` from
    the on-disk path.
    """
    # ``resolve_session_path_default`` walks ``~/.kohakuterrarium/sessions``
    # — a small but synchronous filesystem stat. Off-load it (plus the
    # full cluster-member resolution, which does one stat per member).
    member_paths = await asyncio.to_thread(
        _resolve_cluster_member_paths, session_name, service
    )
    if not member_paths:
        raise HTTPException(404, f"Session not found: {session_name}")

    engine = host_engine_or_none(service)
    # Fan out across cluster members. Standalone shape is a single-entry
    # list — same code path, same query, no per-member overhead beyond
    # one extra ``cluster_groups`` lookup at the top.
    per_member = await asyncio.gather(
        *(
            search_session_memory(
                path,
                q=q,
                mode=mode,
                # Pull ``k`` from EACH member so the top-k merge across
                # the union still has k candidates from every side; trim
                # to k after sort.
                k=k,
                agent=agent,
                engine=engine,
            )
            for _sid, path in member_paths
        )
    )

    # Standalone fast path: single member, no merge bookkeeping needed —
    # return the per-member payload verbatim so its ``count`` /
    # ``session_name`` fields match the pre-CF-5 shape exactly.
    if len(per_member) == 1:
        return per_member[0]

    merged = _merge_member_results(list(per_member), k)
    return {
        "session_name": session_name,
        "query": q,
        "mode": mode,
        "k": k,
        "count": len(merged),
        "results": merged,
    }
