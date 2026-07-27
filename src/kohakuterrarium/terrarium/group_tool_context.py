"""Caller resolution for the group_* tool surface.

Group tools take no graph_id / creature_id self-reference — the caller
is implicit (it's whoever's running the tool). This module turns the
generic :class:`ToolContext` into a :class:`GroupContext` carrying:

- the live :class:`Terrarium` engine,
- the calling :class:`Creature`,
- its current graph topology,

and computes the caller's local group (the live members of its graph).
"""

import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium.channels import TERRARIUM_ENGINE_KEY
from kohakuterrarium.terrarium.graph_identity import (
    CallerGraphNotFoundError,
    GraphIdentityError,
    UnknownCallerError,
    resolve_local_graph_target,
)

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.creature_host import Creature
    from kohakuterrarium.terrarium.engine import Terrarium
    from kohakuterrarium.terrarium.topology import GraphTopology


class GroupToolError(Exception):
    """Raised by group tools to surface a clean error to the model."""


@dataclass
class GroupContext:
    """Resolved tool-call context for a privileged creature."""

    engine: "Terrarium"
    caller: "Creature"
    graph: "GraphTopology"


def resolve_group_context(
    ctx: ToolContext | None, *, require_privileged: bool = True
) -> GroupContext:
    """Resolve the calling creature in the live engine.

    ``require_privileged=True`` (default) rejects non-privileged callers even
    if configuration accidentally grants them a graph-mutating tool.

    ``require_privileged=False`` — accepts any engine creature. Used
    by ``send_channel`` and ``group_send``, which are registered on
    every engine creature and gate themselves per-call (send-edge
    requirement / non-privileged-can-only-target-privileged).

    Raises :class:`GroupToolError` with a model-shaped message when
    the caller cannot be reached or fails the privilege check.
    """
    if ctx is None or ctx.environment is None:
        raise GroupToolError("group tools require a tool context with an environment")
    engine_ref = ctx.environment.get(TERRARIUM_ENGINE_KEY)
    if isinstance(engine_ref, weakref.ref):
        engine = engine_ref()
    else:
        engine = engine_ref
    if engine is None:
        raise GroupToolError("group tools require a live terrarium engine")
    caller_id = getattr(ctx, "creature_id", None)
    if not isinstance(caller_id, str) or not caller_id:
        raise GroupToolError(
            "group tools require ToolContext.creature_id; legacy name-only "
            "contexts are not safe to route"
        )
    caller = engine._creatures.get(caller_id)
    if caller is None or caller.creature_id != caller_id:
        error = UnknownCallerError(caller_id)
        raise GroupToolError(str(error)) from error
    if require_privileged and not getattr(caller, "is_privileged", False):
        raise GroupToolError("this tool is only callable by privileged creatures")
    graph_id = engine._topology.creature_to_graph.get(caller_id)
    graph = engine._topology.graphs.get(graph_id or "")
    if graph is None or caller_id not in graph.creature_ids:
        error = CallerGraphNotFoundError(caller_id, graph_id)
        raise GroupToolError(str(error)) from error
    return GroupContext(engine=engine, caller=caller, graph=graph)


def compute_group(ctx: GroupContext) -> dict[str, "Creature"]:
    """Return live creatures in the caller's graph, keyed by runtime ID."""
    out: dict[str, "Creature"] = {}
    for creature_id in ctx.graph.creature_ids:
        creature = ctx.engine._creatures.get(creature_id)
        if creature is not None and creature.creature_id == creature_id:
            out[creature_id] = creature
    return out


def resolve_group_target(gctx: GroupContext, identifier: str) -> "Creature":
    """Resolve a unique target inside the exact runtime caller's graph.

    Resolution errors are preserved as :class:`GroupToolError` so ambiguous,
    cross-graph, and unknown targets all fail closed rather than silently
    selecting a similarly named creature.
    """
    try:
        resolved = resolve_local_graph_target(
            gctx.engine._topology,
            gctx.engine._creatures,
            caller_id=gctx.caller.creature_id,
            target=identifier,
        )
    except GraphIdentityError as exc:
        raise GroupToolError(str(exc)) from exc
    return gctx.engine._creatures[resolved.target_id]


def engine_is_in_cluster(engine: "Terrarium") -> bool:
    """Heuristic: is this engine part of a Lab cluster?

    Workers stash :class:`TerrariumBroadcastAdapter` and
    :class:`TerrariumOutputWireAdapter` on the engine when they wire it
    into a multi-node Lab cluster (see ``cli/lab_client.py``).  Their
    presence is the cheapest in-engine signal that "this engine is a
    cluster member" — distinct from a host-local standalone engine
    where neither stash exists.  Used by group tools to surface a
    *cluster-aware* error when a target identifier resolves locally to
    nothing: it might be a sibling cluster member living on a peer
    worker that this engine cannot see.
    """
    return (
        getattr(engine, "_broadcast_adapter", None) is not None
        or getattr(engine, "_output_wire_adapter", None) is not None
    )


def cross_cluster_target_error(engine: "Terrarium", identifier: str) -> str:
    """Build the standard ``cross-cluster`` error string used by every
    ``group_*`` tool when ``resolve_group_target`` returns ``None`` on a
    cluster member's engine.  Cluster-wide ``group_*`` routing is not yet
    wired, so surface the cause to distinguish
    a typo from a "lives on another worker" miss.
    """
    if engine_is_in_cluster(engine):
        return (
            f"cross-cluster: target {identifier!r} is not on this worker; "
            "cluster-wide group_* routing is not yet wired (CF-7). The "
            "privileged tool surface only mutates the caller's local "
            "engine; ask the user (Studio) to perform cluster topology "
            "ops for now."
        )
    return f"creature {identifier!r} not in your group"
