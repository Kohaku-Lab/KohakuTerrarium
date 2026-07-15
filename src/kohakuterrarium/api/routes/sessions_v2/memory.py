"""Search saved session memory with full-text, vector, or hybrid modes."""

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
    """Resolve materialized session paths for every reachable cluster member.

    The module-level path resolver remains patchable by route unit tests. Missing
    mirrors are skipped; standalone sessions resolve to their own single path.
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
    """Merge member results by descending score and retain the top ``k``.

    Full-text, vector, and reciprocal-rank fusion results expose comparable scores.
    Missing or invalid scores sort last so they cannot displace scored hits.
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
    """Search session memory without changing indexing behavior.

    Multi-node sessions search every member's mirrored store and merge results by
    score because each worker records its own events. Standalone sessions use the
    same path with one member. When the lab host has no agent engine, searches open
    the on-disk stores directly.
    """
    # Path resolution performs synchronous filesystem stats for every member.
    member_paths = await asyncio.to_thread(
        _resolve_cluster_member_paths, session_name, service
    )
    if not member_paths:
        raise HTTPException(404, f"Session not found: {session_name}")

    engine = host_engine_or_none(service)
    # A single-member standalone session follows the same fan-out path.
    per_member = await asyncio.gather(
        *(
            search_session_memory(
                path,
                q=q,
                mode=mode,
                # Each member contributes up to k candidates before the global trim.
                k=k,
                agent=agent,
                engine=engine,
            )
            for _sid, path in member_paths
        )
    )

    # Preserve the standalone payload fields exactly when no merge is required.
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
