"""Caller resolution for the group_* tool surface.

Group tools take no graph_id / creature_id self-reference — the caller
is implicit (it's whoever's running the tool). This module turns the
generic :class:`ToolContext` into a :class:`GroupContext` carrying:

- the live :class:`Terrarium` engine,
- the calling :class:`Creature`,
- its current graph topology,

and computes the caller's *group* (caller's graph members ∪ caller's
spawned children regardless of which graph they are currently in).
"""

import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kohakuterrarium.modules.tool.base import ToolContext
from kohakuterrarium.terrarium.channels import TERRARIUM_ENGINE_KEY

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

    ``require_privileged=True`` (default) — refuses non-privileged
    callers. Used by graph-mutating tools (status / add / remove /
    start / stop / channel / wire). Belt-and-braces: those tools are
    only force-registered on privileged creatures, but if a config
    grants one to a non-privileged creature this gate still holds.

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
    caller = find_creature(engine, ctx.agent_name)
    if caller is None:
        raise GroupToolError(
            f"caller {ctx.agent_name!r} is not a creature in the engine"
        )
    if require_privileged and not getattr(caller, "is_privileged", False):
        raise GroupToolError("this tool is only callable by privileged creatures")
    graph = engine._topology.graphs.get(caller.graph_id)
    if graph is None:
        raise GroupToolError(
            f"caller's graph {caller.graph_id!r} is not present in topology"
        )
    return GroupContext(engine=engine, caller=caller, graph=graph)


def find_creature(engine: "Terrarium", identifier: str) -> "Creature | None":
    """Look up a creature by ``creature_id``, ``name``, or
    ``agent.config.name``. Returns ``None`` if not found."""
    by_id = engine._creatures.get(identifier)
    if by_id is not None:
        return by_id
    for c in engine._creatures.values():
        if c.name == identifier:
            return c
        cfg = getattr(c.agent, "config", None)
        if cfg is not None and getattr(cfg, "name", None) == identifier:
            return c
    return None


def compute_group(ctx: GroupContext) -> dict[str, "Creature"]:
    """Return all creatures in the caller's group keyed by creature_id.

    Group = (caller's graph members) ∪ (caller's spawned children,
    regardless of which graph they currently live in).
    """
    out: dict[str, "Creature"] = {}
    for cid in ctx.graph.creature_ids:
        c = ctx.engine._creatures.get(cid)
        if c is not None:
            out[cid] = c
    for c in ctx.engine._creatures.values():
        if getattr(c, "parent_creature_id", None) == ctx.caller.creature_id:
            out[c.creature_id] = c
    return out


def resolve_group_target(gctx: GroupContext, identifier: str) -> "Creature | None":
    """Resolve a target identifier (id or name) to a creature in the
    caller's group. Returns ``None`` when the target is not in-group or
    does not exist.
    """
    group = compute_group(gctx)
    target = group.get(identifier)
    if target is not None:
        return target
    for c in group.values():
        if c.name == identifier:
            return c
        cfg = getattr(c.agent, "config", None)
        if cfg is not None and getattr(cfg, "name", None) == identifier:
            return c
    return None
