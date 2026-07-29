"""Resolve creature targets within the exact caller's local graph.

The topology owns graph membership while the creature registry owns live runtime
identities and their human-facing names. This module joins those two pure runtime
views without consulting providers, UI state, remote nodes, or global name
fallbacks.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from kohakuterrarium.terrarium.topology import TopologyState

GraphTargetMatch = Literal["runtime_id", "name"]


class CreatureIdentity(Protocol):
    """The identity fields required from a locally hosted creature."""

    creature_id: str
    name: str


class GraphIdentityError(LookupError):
    """Base class for fail-closed local graph resolution errors."""

    code = "graph_identity_error"

    def __init__(
        self,
        message: str,
        *,
        caller_id: str,
        target: str | None = None,
        graph_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.caller_id = caller_id
        self.target = target
        self.graph_id = graph_id


class UnknownCallerError(GraphIdentityError):
    """The supplied caller is not an exact local runtime identity."""

    code = "unknown_caller"

    def __init__(self, caller_id: str) -> None:
        super().__init__(
            f"caller {caller_id!r} is not a known runtime creature identity",
            caller_id=caller_id,
        )


class GraphNameConflictError(ValueError):
    """Raised when a graph already contains the proposed creature name."""

    def __init__(
        self, *, graph_id: str, name: str, creature_ids: tuple[str, ...]
    ) -> None:
        self.graph_id = graph_id
        self.name = name
        self.creature_ids = creature_ids
        super().__init__(
            f"graph {graph_id!r} already contains creature name {name!r}: "
            + ", ".join(creature_ids)
        )


class CallerGraphNotFoundError(GraphIdentityError):
    """The caller exists, but its graph cannot be determined consistently."""

    code = "caller_graph_not_found"

    def __init__(self, caller_id: str, graph_id: str | None = None) -> None:
        detail = (
            "is not assigned to a graph"
            if graph_id is None
            else f"references unavailable graph {graph_id!r}"
        )
        super().__init__(
            f"caller {caller_id!r} {detail}",
            caller_id=caller_id,
            graph_id=graph_id,
        )


class TargetNotFoundError(GraphIdentityError):
    """No target identity or unique name exists in the caller's graph."""

    code = "target_not_found"

    def __init__(self, caller_id: str, target: str, graph_id: str) -> None:
        super().__init__(
            f"target {target!r} is not a creature in caller {caller_id!r}'s "
            f"graph {graph_id!r}",
            caller_id=caller_id,
            target=target,
            graph_id=graph_id,
        )


class AmbiguousTargetError(GraphIdentityError):
    """A name identifies multiple creatures inside the caller's graph."""

    code = "ambiguous_target"

    def __init__(
        self,
        caller_id: str,
        target: str,
        graph_id: str,
        candidate_ids: tuple[str, ...],
    ) -> None:
        super().__init__(
            f"target {target!r} is ambiguous in caller {caller_id!r}'s graph "
            f"{graph_id!r}; candidates: {', '.join(candidate_ids)}",
            caller_id=caller_id,
            target=target,
            graph_id=graph_id,
        )
        self.candidate_ids = candidate_ids


@dataclass(frozen=True)
class ResolvedGraphTarget:
    """The exact runtime identity selected inside one caller-local graph."""

    graph_id: str
    caller_id: str
    target_id: str
    matched_by: GraphTargetMatch

    @property
    def is_self(self) -> bool:
        """Whether caller and target are the same runtime creature."""
        return self.caller_id == self.target_id


def ensure_graph_name_available(
    topology: TopologyState,
    creatures: Mapping[str, CreatureIdentity],
    *,
    graph_id: str,
    name: str,
    exclude_id: str | None = None,
) -> None:
    """Reject duplicate display or config names inside one logical graph."""
    graph = topology.graphs.get(graph_id)
    member_ids = sorted(graph.creature_ids) if graph is not None else ()
    conflicts = tuple(
        creature_id
        for creature_id in member_ids
        if creature_id != exclude_id
        and (creature := _registered_creature(creatures, creature_id)) is not None
        and name in creature_name_aliases(creature)
    )
    if conflicts:
        raise GraphNameConflictError(
            graph_id=graph_id,
            name=name,
            creature_ids=conflicts,
        )


def resolve_local_graph_target(
    topology: TopologyState,
    creatures: Mapping[str, CreatureIdentity],
    *,
    caller_id: str,
    target: str,
) -> ResolvedGraphTarget:
    """Resolve ``target`` only within an exact runtime caller's graph.

    ``caller_id`` is never interpreted as a display or configuration name. Once
    its graph is known, an exact target runtime ID wins; otherwise ``target``
    must uniquely match a display/configuration name among that graph's members.
    The result deliberately reports self-targeting instead of applying a policy.
    """
    caller = _registered_creature(creatures, caller_id)
    if caller is None:
        raise UnknownCallerError(caller_id)

    graph_id = topology.creature_to_graph.get(caller_id)
    if graph_id is None:
        raise CallerGraphNotFoundError(caller_id)
    graph = topology.graphs.get(graph_id)
    if graph is None or caller_id not in graph.creature_ids:
        raise CallerGraphNotFoundError(caller_id, graph_id)

    if target in graph.creature_ids:
        exact_target = _registered_creature(creatures, target)
        if exact_target is None:
            raise TargetNotFoundError(caller_id, target, graph_id)
        return ResolvedGraphTarget(
            graph_id=graph_id,
            caller_id=caller_id,
            target_id=target,
            matched_by="runtime_id",
        )

    candidate_ids = tuple(
        creature_id
        for creature_id in sorted(graph.creature_ids)
        if (creature := _registered_creature(creatures, creature_id)) is not None
        and target in creature_name_aliases(creature)
    )
    if not candidate_ids:
        raise TargetNotFoundError(caller_id, target, graph_id)
    if len(candidate_ids) > 1:
        raise AmbiguousTargetError(caller_id, target, graph_id, candidate_ids)
    return ResolvedGraphTarget(
        graph_id=graph_id,
        caller_id=caller_id,
        target_id=candidate_ids[0],
        matched_by="name",
    )


def _registered_creature(
    creatures: Mapping[str, CreatureIdentity], creature_id: str
) -> CreatureIdentity | None:
    creature = creatures.get(creature_id)
    if creature is None or creature.creature_id != creature_id:
        return None
    return creature


def creature_name_aliases(creature: CreatureIdentity) -> set[str]:
    """Return every display/config name accepted by graph-local resolution."""
    aliases = {creature.name} if creature.name else set()
    config = getattr(creature, "config", None)
    _add_name(aliases, config)
    agent = getattr(creature, "agent", None)
    _add_name(aliases, getattr(agent, "config", None))
    return aliases


def _add_name(aliases: set[str], value: object) -> None:
    if isinstance(value, Mapping):
        name = value.get("name")
    else:
        name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        aliases.add(name)


__all__ = [
    "AmbiguousTargetError",
    "CallerGraphNotFoundError",
    "CreatureIdentity",
    "GraphIdentityError",
    "GraphNameConflictError",
    "GraphTargetMatch",
    "ResolvedGraphTarget",
    "TargetNotFoundError",
    "UnknownCallerError",
    "creature_name_aliases",
    "ensure_graph_name_available",
    "resolve_local_graph_target",
]
