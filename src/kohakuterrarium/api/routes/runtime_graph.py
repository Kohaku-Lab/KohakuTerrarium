"""Runtime graph snapshot API for the graph editor.

The graph editor needs one normalized, process-local view of the live
Terrarium engine: graphs, creatures, shared channels, and direct output
wiring. This route is read-only and returns backend/runtime data only;
frontend layout state remains a UI preference.
"""

import json
import time
from typing import Any

from fastapi import APIRouter, Depends

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.topology import GraphTopology

router = APIRouter()


@router.get("/graph")
async def runtime_graph_snapshot(engine: Terrarium = Depends(get_engine)):
    """Return a normalized snapshot of every live runtime graph."""
    return build_runtime_graph_snapshot(engine)


def build_runtime_graph_snapshot(engine: Terrarium) -> dict[str, Any]:
    """Build the graph-editor snapshot from the live engine.

    Graphs are ordered by their session ``created_at`` timestamp
    (oldest first, newest last) so the graph editor's auto-layout
    appends new creatures to the right of existing ones rather than
    shoving them in alphabetically by id. ``graph_id`` breaks ties.
    """

    def _order_key(graph: GraphTopology) -> tuple[str, str]:
        meta = lifecycle.get_session_meta(graph.graph_id)
        return (meta.get("created_at", ""), graph.graph_id)

    graphs = []
    for graph in sorted(engine.list_graphs(), key=_order_key):
        graphs.append(_graph_to_dict(engine, graph))
    return {
        "version": int(time.time() * 1000),
        "graphs": graphs,
    }


def _graph_to_dict(engine: Terrarium, graph: GraphTopology) -> dict[str, Any]:
    meta = lifecycle.get_session_meta(graph.graph_id)
    creatures = _creatures_for_graph(engine, graph)
    channels = _channels_for_graph(engine, graph)
    return {
        "graph_id": graph.graph_id,
        "kind": meta.get("kind") or ("terrarium" if len(creatures) > 1 else "creature"),
        "name": meta.get("name") or graph.graph_id,
        "created_at": meta.get("created_at", ""),
        "config_path": meta.get("config_path", ""),
        "pwd": meta.get("pwd", ""),
        "has_root": bool(meta.get("has_root", False)),
        "creatures": creatures,
        "channels": channels,
        "output_edges": _output_edges_for_graph(engine, graph, creatures),
    }


def _creatures_for_graph(
    engine: Terrarium, graph: GraphTopology
) -> list[dict[str, Any]]:
    """Serialize every creature in ``graph`` for the snapshot.

    ``is_root`` is retained as a synonym for legacy frontend code.
    With multiple privileged creatures in one graph (possible after
    merging two solo sessions, or hot-plugging a second user-spawn),
    only one creature is tagged as the root: the one with
    ``creature_id == "root"`` (recipe convention) or, failing that,
    ``name == "root"``. ``is_privileged`` is the canonical flag and
    every privileged creature carries it.
    """
    creatures: list[dict[str, Any]] = []
    privileged_ids: list[str] = []
    raw_creatures: list = []
    for creature_id in sorted(graph.creature_ids):
        try:
            creature = engine.get_creature(creature_id)
        except KeyError:
            continue
        raw_creatures.append((creature_id, creature))
        if getattr(creature, "is_privileged", False):
            privileged_ids.append(creature_id)

    root_id = ""
    if privileged_ids:
        # Recipe convention first (``creature_id == "root"``), then
        # ``name == "root"``, then the lowest-sorted privileged id.
        for cid in privileged_ids:
            if cid == "root":
                root_id = cid
                break
        if not root_id:
            for cid in privileged_ids:
                c = engine._creatures.get(cid)
                if c is not None and c.name == "root":
                    root_id = cid
                    break
        if not root_id:
            root_id = privileged_ids[0]

    for creature_id, creature in raw_creatures:
        status = dict(creature.get_status())
        status["is_privileged"] = bool(getattr(creature, "is_privileged", False))
        status["is_root"] = creature_id == root_id
        status["parent_creature_id"] = getattr(creature, "parent_creature_id", None)
        status["graph_id"] = graph.graph_id
        creatures.append(status)
    return creatures


def _channels_for_graph(
    engine: Terrarium, graph: GraphTopology
) -> list[dict[str, Any]]:
    env = engine._environments.get(graph.graph_id)
    registry = getattr(env, "shared_channels", None) if env is not None else None
    names = set(graph.channels)
    if registry is not None:
        names.update(registry.list_channels())

    channels: list[dict[str, Any]] = []
    for name in sorted(names):
        topo_info = graph.channels.get(name)
        runtime_channel = registry.get(name) if registry is not None else None
        channel_type = ""
        description = ""
        if topo_info is not None:
            description = getattr(topo_info, "description", "") or ""
        if runtime_channel is not None:
            channel_type = channel_type or getattr(runtime_channel, "channel_type", "")
            description = (
                description or getattr(runtime_channel, "description", "") or ""
            )
        history = list(getattr(runtime_channel, "history", []) or [])
        channels.append(
            {
                "name": name,
                "type": channel_type or "broadcast",
                "description": description,
                "qsize": int(getattr(runtime_channel, "qsize", 0) or 0),
                "message_count": len(history),
                "last_message": _message_to_dict(history[-1]) if history else None,
            }
        )
    return channels


def _output_edges_for_graph(
    engine: Terrarium,
    graph: GraphTopology,
    creatures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for creature in creatures:
        creature_id = creature.get("creature_id") or creature.get("agent_id")
        if not creature_id:
            continue
        try:
            output_edges = engine.list_output_wiring(creature_id)
        except Exception:
            output_edges = []
        for edge in output_edges:
            edge_dict = dict(edge)
            edge_id = edge_dict.get("edge_id") or edge_dict.get("id", "")
            target = edge_dict.get("to", "")
            edge_dict["edge_id"] = edge_id
            edge_dict["from"] = creature_id
            edge_dict["from_name"] = creature.get("name", "")
            edge_dict["to_creature_id"] = _resolve_target_creature_id(
                graph, creatures, target
            )
            edge_dict["graph_id"] = graph.graph_id
            edges.append(edge_dict)
    return edges


def _resolve_target_creature_id(
    graph: GraphTopology,
    creatures: list[dict[str, Any]],
    target: str,
) -> str:
    if not target:
        return ""
    by_id = {
        c.get("creature_id") or c.get("agent_id"): c
        for c in creatures
        if c.get("creature_id") or c.get("agent_id")
    }
    if target in by_id:
        return target
    for creature in creatures:
        creature_id = creature.get("creature_id") or creature.get("agent_id") or ""
        if target == creature.get("name"):
            return creature_id
        if target == "root" and creature.get("is_root"):
            return creature_id
    if target in graph.creature_ids:
        return target
    return ""


def _message_to_dict(message: Any) -> dict[str, Any]:
    return {
        "message_id": getattr(message, "message_id", ""),
        "sender": getattr(message, "sender", ""),
        "content": _jsonable(getattr(message, "content", "")),
        "content_preview": _preview(getattr(message, "content", "")),
        "timestamp": _timestamp_to_string(getattr(message, "timestamp", None)),
        "metadata": _jsonable(getattr(message, "metadata", {}) or {}),
        "reply_to": getattr(message, "reply_to", None),
    }


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _preview(value: Any, limit: int = 160) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except TypeError:
            text = str(value)
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _timestamp_to_string(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)
