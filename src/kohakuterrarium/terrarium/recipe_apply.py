"""Transactional mechanics for applying a resolved terrarium recipe."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.topology as _topo
import kohakuterrarium.terrarium.wiring as _wiring
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.terrarium.config import CreatureConfig, TerrariumConfig
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.recipe_transaction import (
    RecipeApplyTransaction,
    rollback_shielded,
)
from kohakuterrarium.terrarium.topology import GraphTopology
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium

CreatureBuilder = Callable[..., Creature]
logger = get_logger(__name__)


async def apply_resolved_recipe(
    engine: Terrarium,
    config: TerrariumConfig,
    *,
    runtime_ids: dict[str, str],
    graph: GraphTopology | str | None,
    pwd: str | None,
    llm: Any,
    strict: bool,
    start: bool,
    builder: CreatureBuilder,
    use_default_builder: bool,
    created_ids: list[str] | None,
    transaction: RecipeApplyTransaction | None = None,
) -> GraphTopology:
    """Apply a validated recipe and roll back every owned mutation on failure."""
    transaction = transaction or RecipeApplyTransaction(engine)
    try:
        result = await _apply(
            engine,
            config,
            runtime_ids=runtime_ids,
            graph=graph,
            pwd=pwd,
            llm=llm,
            strict=strict,
            start=start,
            builder=builder,
            use_default_builder=use_default_builder,
            transaction=transaction,
        )
    except BaseException:
        await rollback_shielded(transaction)
        raise

    if created_ids is not None:
        created_ids.extend(transaction.created_creature_ids)
    # The growth prompt reports this cap, so the graph has to carry it.
    if config.max_creatures:
        result.max_creatures = config.max_creatures
    return result


async def _apply(
    engine: Terrarium,
    config: TerrariumConfig,
    *,
    runtime_ids: dict[str, str],
    graph: GraphTopology | str | None,
    pwd: str | None,
    llm: Any,
    strict: bool,
    start: bool,
    builder: CreatureBuilder,
    use_default_builder: bool,
    transaction: RecipeApplyTransaction,
) -> GraphTopology:
    graph_id, graph_obj = _prepare_graph(engine, graph, transaction)
    transaction.snapshot_existing_members(graph_id)
    environment = engine._environments[graph_id]
    _channels.register_engine_handle(environment, engine)
    await _declare_channels(engine, config, graph_id, transaction)

    root_creature = await _add_members(
        engine,
        config,
        runtime_ids=runtime_ids,
        graph_id=graph_id,
        pwd=pwd,
        llm=llm,
        strict=strict,
        builder=builder,
        use_default_builder=use_default_builder,
        transaction=transaction,
        environment=environment,
    )
    await _wire_members(engine, config, runtime_ids, root_creature, environment)

    if start:
        for creature_id in transaction.created_creature_ids:
            creature = engine.get_creature(creature_id)
            await creature.start()
            if engine._drive_runtime is not None:
                engine._drive_runtime.schedule_reconcile(creature)

    logger.info(
        "Applied recipe '%s': %d creatures, %d channels",
        config.name,
        len(config.creatures) + (1 if config.root else 0),
        len(config.channels) + (1 if config.root else 0),
    )
    return graph_obj


def _prepare_graph(
    engine: Terrarium,
    graph: GraphTopology | str | None,
    transaction: RecipeApplyTransaction,
) -> tuple[str, GraphTopology]:
    if graph is not None:
        graph_id = engine._resolve_graph_id(graph)
        return graph_id, engine.get_graph(graph_id)

    graph_id = _topo.new_graph_id()
    graph_obj = _topo.GraphTopology(graph_id=graph_id)
    engine._topology.graphs[graph_id] = graph_obj
    engine._environments[graph_id] = Environment(env_id=f"env_{graph_id}")
    transaction.record_graph(graph_id)
    return graph_id, graph_obj


async def _declare_channels(
    engine: Terrarium,
    config: TerrariumConfig,
    graph_id: str,
    transaction: RecipeApplyTransaction,
) -> None:
    graph = engine.get_graph(graph_id)
    for channel in config.channels:
        if channel.name not in graph.channels:
            await engine.add_channel(
                graph_id,
                channel.name,
                description=channel.description,
            )
            transaction.record_channel(graph_id, channel.name)

    for creature in config.creatures:
        if creature.name not in engine.get_graph(graph_id).channels:
            await engine.add_channel(
                graph_id,
                creature.name,
                description=f"Direct channel to {creature.name}",
            )
            transaction.record_channel(graph_id, creature.name)

    if (
        config.root is not None
        and "report_to_root" not in engine.get_graph(graph_id).channels
    ):
        await engine.add_channel(
            graph_id,
            "report_to_root",
            description="Any creature can report to the root",
        )
        transaction.record_channel(graph_id, "report_to_root")


async def _add_members(
    engine: Terrarium,
    config: TerrariumConfig,
    *,
    runtime_ids: dict[str, str],
    graph_id: str,
    pwd: str | None,
    llm: Any,
    strict: bool,
    builder: CreatureBuilder,
    use_default_builder: bool,
    transaction: RecipeApplyTransaction,
    environment: Environment,
) -> Creature | None:
    for creature_config in config.creatures:
        creature = _build_recipe_creature(
            builder,
            creature_config,
            creature_id=runtime_ids[creature_config.name],
            graph_id=graph_id,
            pwd=pwd,
            llm=llm,
            strict=strict,
            use_default_builder=use_default_builder,
            environment=environment,
        )
        if creature.creature_id != runtime_ids[creature_config.name]:
            raise ValueError(
                f"recipe builder returned creature_id {creature.creature_id!r}; "
                f"reserved {runtime_ids[creature_config.name]!r}"
            )
        transaction.record_creature(creature.creature_id)
        await engine.add_creature(
            creature,
            graph=graph_id,
            start=False,
            session=False,
            _identity_reserved=True,
        )

    if config.root is None:
        return None

    root_data = dict(config.root.config_data)
    root_data["name"] = "root"
    root_config = CreatureConfig(
        name="root",
        config_data=root_data,
        base_dir=config.root.base_dir,
    )
    root = _build_recipe_creature(
        builder,
        root_config,
        creature_id=runtime_ids["root"],
        graph_id=graph_id,
        pwd=pwd,
        llm=llm,
        strict=strict,
        use_default_builder=use_default_builder,
        environment=environment,
    )
    if root.creature_id != runtime_ids["root"]:
        raise ValueError(
            f"recipe builder returned creature_id {root.creature_id!r}; "
            f"reserved {runtime_ids['root']!r}"
        )
    transaction.record_creature(root.creature_id)
    return await engine.add_creature(
        root,
        graph=graph_id,
        start=False,
        session=False,
        _identity_reserved=True,
    )


async def _wire_members(
    engine: Terrarium,
    config: TerrariumConfig,
    runtime_ids: dict[str, str],
    root_creature: Creature | None,
    environment: Environment,
) -> None:
    for creature_config in config.creatures:
        creature = engine.get_creature(runtime_ids[creature_config.name])
        listen_channels = list(
            dict.fromkeys([creature_config.name, *creature_config.listen_channels])
        )
        for channel_name in listen_channels:
            channel = environment.shared_channels.get(channel_name)
            if channel is None:
                continue
            _channels.inject_channel_trigger(
                creature.agent,
                subscriber_id=creature.name,
                channel_name=channel_name,
                registry=environment.shared_channels,
                ignore_sender_id=creature.creature_id,
            )
            if channel_name not in creature.listen_channels:
                creature.listen_channels.append(channel_name)
            graph = engine._topology.graphs[creature.graph_id]
            graph.listen_edges.setdefault(creature.creature_id, set()).add(channel_name)

        for channel_name in creature_config.send_channels:
            graph = engine._topology.graphs[creature.graph_id]
            if channel_name not in graph.channels:
                continue
            if channel_name not in creature.send_channels:
                creature.send_channels.append(channel_name)
            graph.send_edges.setdefault(creature.creature_id, set()).add(channel_name)

    if root_creature is not None:
        await engine.assign_root(root_creature)

    _wiring.install_output_wiring_resolver(engine)


def _build_recipe_creature(
    builder: CreatureBuilder,
    config: CreatureConfig,
    *,
    creature_id: str,
    graph_id: str,
    pwd: str | None,
    llm: Any,
    strict: bool,
    use_default_builder: bool,
    environment: Environment,
) -> Creature:
    kwargs: dict[str, Any] = {"creature_id": creature_id, "pwd": pwd}
    if use_default_builder:
        kwargs.update(
            graph_id=graph_id,
            llm=llm,
            strict=strict,
            environment=environment,
        )
    creature = builder(config, **kwargs)
    if not use_default_builder:
        creature.agent.environment = environment
        executor = getattr(creature.agent, "executor", None)
        if executor is not None:
            executor._environment = environment
    return creature
