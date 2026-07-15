"""Unresolved-replay-remnant bookkeeping shared by snapshot + merge/split.

This leaf module keeps shared helpers out of ``topology_snapshot`` and
``session_coord``, which would otherwise form a runtime import cycle.
"""

import copy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium


def merge_into(payload: dict[str, Any], leftovers: dict[str, Any]) -> None:
    """Union an incomplete replay's unresolved remnant into ``payload``."""
    have = {c.get("name") for c in payload["channels"]}
    for ch in leftovers.get("channels", []):
        if ch.get("name") not in have:
            payload["channels"].append(ch)
    for edge_name in ("listen_edges", "send_edges"):
        for cid, chans in leftovers.get(edge_name, {}).items():
            merged = set(payload[edge_name].get(cid, [])) | set(chans)
            payload[edge_name][cid] = sorted(merged)


def transfer_leftovers(
    engine: "Terrarium", source_gids: list[str], target_gid: str
) -> None:
    """Graph-merge bookkeeping: unresolved replay remnants must follow
    the surviving graph, or the post-merge snapshot erases them."""
    leftover_map = getattr(engine, "_topology_replay_leftovers", None)
    if not leftover_map:
        return
    merged: dict[str, Any] = {"channels": [], "listen_edges": {}, "send_edges": {}}
    found = False
    for gid in dict.fromkeys([target_gid, *source_gids]):
        entry = leftover_map.pop(gid, None)
        if entry:
            merge_into(merged, entry)
            found = True
    if found:
        leftover_map[target_gid] = merged


def distribute_leftovers(
    engine: "Terrarium", parent_gid: str, child_gids: list[str]
) -> None:
    """Graph-split bookkeeping: every child inherits the parent's
    unresolved remnant — a superset is safe (replay is additive and
    unresolved entries simply persist until they resolve)."""
    leftover_map = getattr(engine, "_topology_replay_leftovers", None)
    if not leftover_map:
        return
    entry = leftover_map.pop(parent_gid, None)
    if not entry:
        return
    for gid in child_gids:
        merged: dict[str, Any] = {
            "channels": [],
            "listen_edges": {},
            "send_edges": {},
        }
        existing = leftover_map.get(gid)
        if existing:
            merge_into(merged, existing)
        merge_into(merged, copy.deepcopy(entry))
        leftover_map[gid] = merged
