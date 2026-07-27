"""Resolve display names to canonical creature identifiers for session routes."""

from fastapi import HTTPException

from kohakuterrarium.terrarium.multi_node_cluster import cluster_groups
from kohakuterrarium.terrarium.service import TerrariumService


def _cluster_scope(service: TerrariumService, session_id: str) -> set[str]:
    """Return every graph identifier in the session's cluster.

    Cross-node creatures retain worker-local graph identifiers while clients use
    the cluster primary. Expanding the scope lets session routes resolve members on
    peer workers; standalone sessions remain scoped to the requested identifier.
    """
    for members in cluster_groups(service).values():
        if session_id in members:
            return set(members)
    return {session_id}


async def resolve_connect_target_id(
    service: TerrariumService,
    name_or_id: str,
    session_id: str,
) -> str:
    """Allow an explicit runtime ID across graphs, never a name fallback."""
    try:
        creatures = await service.list_creatures()
    except Exception as exc:  # noqa: BLE001 — all service failures map to 503
        raise HTTPException(503, f"service unavailable: {exc}") from exc
    for info in creatures:
        if info.creature_id == name_or_id:
            return info.creature_id
    return await resolve_creature_id(service, name_or_id, session_id)


async def resolve_creature_id(
    service: TerrariumService,
    name_or_id: str,
    session_id: str | None = None,
) -> str:
    """Return the canonical creature identifier for an identifier or name.

    Exact identifiers take precedence over display names. Session-scoped callers
    must pass ``session_id`` so duplicate names in different sessions cannot resolve
    to the wrong transcript or runtime state. Cluster scope includes worker-local
    graph identifiers; ``None`` retains global lookup for legacy callers. A creature
    outside the requested scope is treated as not found.
    """
    try:
        creatures = await service.list_creatures()
    except Exception as exc:  # noqa: BLE001 — all service failures map to 503
        raise HTTPException(503, f"service unavailable: {exc}") from exc

    if session_id:
        allowed = _cluster_scope(service, session_id)
        creatures = tuple(c for c in creatures if c.graph_id in allowed)

    # Exact identifiers take precedence over display names.
    for info in creatures:
        if info.creature_id == name_or_id:
            return info.creature_id
    # Name lookup remains within the requested session or cluster and must be unique.
    matches = [
        info.creature_id
        for info in creatures
        if info.name == name_or_id
        or (
            bool(config_name := getattr(info, "config_name", ""))
            and config_name == name_or_id
        )
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise HTTPException(
            409,
            f"creature name {name_or_id!r} is ambiguous in this session",
        )
    raise HTTPException(404, f"creature {name_or_id!r} not found")


__all__ = ["resolve_connect_target_id", "resolve_creature_id"]
