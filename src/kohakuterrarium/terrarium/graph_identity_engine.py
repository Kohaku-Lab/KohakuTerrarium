"""Engine mutation guards for graph-local creature identities."""

from typing import TYPE_CHECKING, Any

from kohakuterrarium.terrarium.graph_identity import (
    GraphNameConflictError,
    ensure_graph_name_available,
)

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.creature_host import Creature, CreatureRef
    from kohakuterrarium.terrarium.engine import Terrarium


def bind_runtime_creature_id(creature: "Creature") -> None:
    """Expose the final engine runtime ID to tool execution contexts."""
    executor = getattr(creature.agent, "executor", None)
    if executor is not None:
        executor._creature_id = creature.creature_id


def guard_add_name(
    engine: "Terrarium", creature: "Creature", graph_id: str | None
) -> None:
    """Reject a duplicate role name when adding into an existing graph."""
    if graph_id is None:
        return
    ensure_graph_name_available(
        engine._topology,
        engine._creatures,
        graph_id=graph_id,
        name=creature.name,
    )


def _graph_aliases(engine: "Terrarium", graph_id: str) -> dict[str, set[str]]:
    graph = engine._topology.graphs.get(graph_id)
    aliases: dict[str, set[str]] = {}
    if graph is None:
        return aliases
    for creature_id in graph.creature_ids:
        creature = engine._creatures.get(creature_id)
        if creature is None:
            continue
        config = getattr(getattr(creature, "agent", None), "config", None)
        for alias in {creature.name, getattr(config, "name", None)}:
            if alias:
                aliases.setdefault(alias, set()).add(creature_id)
    return aliases


def resolve_and_guard_connect(
    engine: "Terrarium",
    sender: "CreatureRef",
    receiver: "CreatureRef",
) -> tuple[Any, Any]:
    """Resolve endpoints and reject merges that create duplicate names."""
    sender_creature = engine.get_creature(engine._resolve_creature_id(sender))
    receiver_creature = engine.get_creature(engine._resolve_creature_id(receiver))
    sender_graph_id = engine._topology.creature_to_graph.get(
        sender_creature.creature_id
    )
    receiver_graph_id = engine._topology.creature_to_graph.get(
        receiver_creature.creature_id
    )
    if (
        sender_graph_id != receiver_graph_id
        and sender_graph_id is not None
        and receiver_graph_id is not None
    ):
        sender_aliases = _graph_aliases(engine, sender_graph_id)
        receiver_aliases = _graph_aliases(engine, receiver_graph_id)
        conflicts = sorted(set(sender_aliases) & set(receiver_aliases))
        if conflicts:
            alias = conflicts[0]
            creature_ids = tuple(
                sorted(sender_aliases[alias] | receiver_aliases[alias])
            )
            raise GraphNameConflictError(
                graph_id=f"{sender_graph_id}+{receiver_graph_id}",
                name=alias,
                creature_ids=creature_ids,
            )
    return sender_creature, receiver_creature
