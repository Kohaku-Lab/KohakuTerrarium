"""Cluster-aware session-file path resolution.

After a cross-node ``connect()`` records a ``_cluster_links`` entry on
the ``MultiNodeTerrariumService``, every cluster member writes its OWN
per-worker session store. Host-side, those are mirrored under
``<session_dir>/mirror/<member_sid>.kohakutr``. Any read-only endpoint
that surfaces "everything in this session" (memory search, viewer
tree / summary / turns / events, …) MUST walk every member's file —
opening only the primary's store hides the non-primary workers'
history. CF-5 fixed memory search; the viewer endpoints share the same
blind spot and use this helper for the same fan-out.

This module is pure: it takes a ``TerrariumService`` (read its
``_cluster_links``) and the requested sid, returns the on-disk paths
for every reachable cluster member. No I/O beyond a directory-scan
``stat`` per member sid.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from kohakuterrarium.studio.persistence.store import resolve_session_path_default
from kohakuterrarium.studio.sessions import cluster_fold

if TYPE_CHECKING:
    from kohakuterrarium.terrarium import TerrariumService


def resolve_cluster_member_paths(
    session_sid: str, service: "TerrariumService"
) -> list[tuple[str, Path]]:
    """Return ``[(member_sid, path)]`` for every reachable cluster member.

    ``session_sid`` may be EITHER the cluster primary or a non-primary
    member — both map to the same primary via
    :func:`cluster_fold.sid_to_primary`, so a GET on either sid walks
    the whole group. Members are returned in lex-sorted ``member_sid``
    order so callers can rely on deterministic ordering when merging
    payloads.

    Standalone mode (no ``_cluster_links``): returns a single-entry
    list with ``session_sid``'s own resolved path, so callers fall
    through to the existing scalar code path unchanged. An empty list
    means the requested sid is unknown on disk and the caller should
    surface a 404.

    Members whose mirror has not yet materialised (worker hasn't teed
    any event, or the file was purged) are silently skipped — without
    this a freshly-spawned cluster would 404 before its first chat
    turn lands an event in the mirror.
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


__all__ = ["resolve_cluster_member_paths"]
