"""Unit tests for caller-scoped creature identity resolution."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium.graph_identity import (
    AmbiguousTargetError,
    CallerGraphNotFoundError,
    TargetNotFoundError,
    UnknownCallerError,
    resolve_local_graph_target,
)
from kohakuterrarium.terrarium.topology import TopologyState, add_creature


@dataclass
class _Creature:
    creature_id: str
    name: str
    config_name: str | None = None

    def __post_init__(self) -> None:
        config_name = self.config_name or self.name
        self.agent = SimpleNamespace(config=SimpleNamespace(name=config_name))


def _two_graphs():
    topology = TopologyState()
    creatures: dict[str, _Creature] = {}

    graph_a = add_creature(topology, "root-a")
    add_creature(topology, "reviewer-a", graph_id=graph_a)
    graph_b = add_creature(topology, "root-b")
    add_creature(topology, "reviewer-b", graph_id=graph_b)

    creatures["root-a"] = _Creature("root-a", "root")
    creatures["reviewer-a"] = _Creature("reviewer-a", "reviewer")
    creatures["root-b"] = _Creature("root-b", "root")
    creatures["reviewer-b"] = _Creature("reviewer-b", "reviewer")
    return topology, creatures, graph_a, graph_b


def test_exact_runtime_id_wins_over_name_match():
    topology = TopologyState()
    graph_id = add_creature(topology, "caller-id")
    add_creature(topology, "exact-id", graph_id=graph_id)
    add_creature(topology, "named-id", graph_id=graph_id)
    creatures = {
        "caller-id": _Creature("caller-id", "caller"),
        "exact-id": _Creature("exact-id", "other"),
        "named-id": _Creature("named-id", "exact-id"),
    }

    resolved = resolve_local_graph_target(
        topology, creatures, caller_id="caller-id", target="exact-id"
    )

    assert resolved.target_id == "exact-id"
    assert resolved.matched_by == "runtime_id"
    assert resolved.graph_id == graph_id


@pytest.mark.parametrize("use_config_name", [False, True])
def test_unique_display_or_config_name_resolves_inside_caller_graph(use_config_name):
    topology, creatures, graph_a, _ = _two_graphs()
    target = "reviewer"
    if use_config_name:
        creatures["reviewer-a"].name = "visible reviewer"
        target = creatures["reviewer-a"].agent.config.name

    resolved = resolve_local_graph_target(
        topology, creatures, caller_id="root-a", target=target
    )

    assert resolved.target_id == "reviewer-a"
    assert resolved.graph_id == graph_a
    assert resolved.matched_by == "name"


def test_cross_graph_same_names_never_cross_route():
    topology, creatures, graph_a, graph_b = _two_graphs()

    from_a = resolve_local_graph_target(
        topology, creatures, caller_id="root-a", target="reviewer"
    )
    from_b = resolve_local_graph_target(
        topology, creatures, caller_id="root-b", target="reviewer"
    )

    assert (from_a.target_id, from_a.graph_id) == ("reviewer-a", graph_a)
    assert (from_b.target_id, from_b.graph_id) == ("reviewer-b", graph_b)


def test_two_graphs_each_resolve_their_own_root_and_reviewer():
    topology, creatures, _, _ = _two_graphs()

    assert (
        resolve_local_graph_target(
            topology, creatures, caller_id="reviewer-a", target="root"
        ).target_id
        == "root-a"
    )
    assert (
        resolve_local_graph_target(
            topology, creatures, caller_id="root-a", target="reviewer"
        ).target_id
        == "reviewer-a"
    )
    assert (
        resolve_local_graph_target(
            topology, creatures, caller_id="reviewer-b", target="root"
        ).target_id
        == "root-b"
    )
    assert (
        resolve_local_graph_target(
            topology, creatures, caller_id="root-b", target="reviewer"
        ).target_id
        == "reviewer-b"
    )


def test_same_graph_name_ambiguity_fails_closed():
    topology, creatures, graph_a, _ = _two_graphs()
    add_creature(topology, "reviewer-a-2", graph_id=graph_a)
    creatures["reviewer-a-2"] = _Creature("reviewer-a-2", "reviewer")

    with pytest.raises(AmbiguousTargetError) as caught:
        resolve_local_graph_target(
            topology, creatures, caller_id="root-a", target="reviewer"
        )

    assert caught.value.code == "ambiguous_target"
    assert caught.value.graph_id == graph_a
    assert caught.value.candidate_ids == ("reviewer-a", "reviewer-a-2")


def test_unknown_caller_name_does_not_fall_back_globally():
    topology, creatures, _, _ = _two_graphs()

    with pytest.raises(UnknownCallerError) as caught:
        resolve_local_graph_target(
            topology, creatures, caller_id="root", target="reviewer"
        )

    assert caught.value.code == "unknown_caller"
    assert caught.value.caller_id == "root"


@pytest.mark.parametrize("missing_graph", [False, True])
def test_caller_without_resolvable_graph_fails_closed(missing_graph):
    topology, creatures, graph_a, _ = _two_graphs()
    if missing_graph:
        topology.graphs.pop(graph_a)
    else:
        topology.creature_to_graph.pop("root-a")

    with pytest.raises(CallerGraphNotFoundError) as caught:
        resolve_local_graph_target(
            topology, creatures, caller_id="root-a", target="reviewer"
        )

    assert caught.value.code == "caller_graph_not_found"


def test_unknown_target_does_not_fall_back_to_other_graph():
    topology, creatures, graph_a, _ = _two_graphs()
    creatures["reviewer-a"].name = "critic"
    creatures["reviewer-a"].agent.config.name = "critic"

    with pytest.raises(TargetNotFoundError) as caught:
        resolve_local_graph_target(
            topology, creatures, caller_id="root-a", target="reviewer"
        )

    assert caught.value.code == "target_not_found"
    assert caught.value.graph_id == graph_a


def test_self_target_is_returned_for_caller_policy_to_decide():
    topology, creatures, graph_a, _ = _two_graphs()

    by_id = resolve_local_graph_target(
        topology, creatures, caller_id="root-a", target="root-a"
    )
    by_name = resolve_local_graph_target(
        topology, creatures, caller_id="root-a", target="root"
    )

    assert by_id.target_id == "root-a"
    assert by_id.is_self is True
    assert by_id.matched_by == "runtime_id"
    assert by_name.target_id == "root-a"
    assert by_name.is_self is True
    assert by_name.matched_by == "name"
    assert by_name.graph_id == graph_a


def test_stale_runtime_id_in_topology_is_not_a_valid_target():
    topology, creatures, graph_a, _ = _two_graphs()
    add_creature(topology, "stale-id", graph_id=graph_a)

    with pytest.raises(TargetNotFoundError):
        resolve_local_graph_target(
            topology, creatures, caller_id="root-a", target="stale-id"
        )
