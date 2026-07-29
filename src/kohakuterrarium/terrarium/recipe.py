"""Recipe loader — apply a ``TerrariumConfig`` to a Terrarium engine.

A recipe is just a YAML / dataclass description of "add these creatures,
declare these channels, wire these listen/send edges."  The engine has
all the primitives needed; this file is the thin glue that walks a
recipe and calls them in dependency order.

Auto-created channels preserve the recipe contract:

- One channel named after each creature — the "direct" channel any
  other creature can address. (Graph topology channels are always
  broadcast.)
- ``report_to_root`` channel when the recipe declares a root.

When a root is declared it is built like any other creature, then the
engine's :meth:`Terrarium.assign_root` is called against it.
``assign_root`` sets ``creature.is_privileged = True``, wires the root
as listener on every existing channel (including ``report_to_root``),
and gives every other creature a send edge on ``report_to_root``.
``assign_root`` also calls
:func:`terrarium.tools_group.force_register_group_tools` on the root
agent, so no recipe-side tool injection is needed.
"""

import asyncio
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from kohakuterrarium.core.environment import Environment
from kohakuterrarium.terrarium.config import (
    CreatureConfig,
    TerrariumConfig,
    load_terrarium_config,
)
from kohakuterrarium.terrarium.creature_host import Creature, build_creature
from kohakuterrarium.terrarium.graph_identity import ensure_graph_name_available
from kohakuterrarium.terrarium.recipe_apply import apply_resolved_recipe
from kohakuterrarium.terrarium.topology import GraphTopology

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium

CreatureBuilder = Callable[..., Creature]


def _resolve_recipe(
    recipe: TerrariumConfig | str | Path,
) -> TerrariumConfig:
    if isinstance(recipe, TerrariumConfig):
        return recipe
    return load_terrarium_config(recipe)


async def apply_recipe(
    engine: "Terrarium",
    recipe: TerrariumConfig | str | Path,
    *,
    graph: GraphTopology | str | None = None,
    pwd: str | None = None,
    llm: Any = None,
    strict: bool = True,
    start: bool = True,
    creature_builder: CreatureBuilder | None = None,
    created_ids: list[str] | None = None,
    transaction=None,
) -> GraphTopology:
    """Load a terrarium recipe into ``engine`` and return the resulting
    :class:`GraphTopology`.

    All creatures land in a single graph (created fresh when ``graph``
    is None).  ``creature_builder`` defaults to
    :func:`terrarium.creature_host.build_creature`; tests pass a stub
    that returns fake-Agent creatures.

    When ``created_ids`` is provided, each creature's final id is appended
    to it as the add succeeds, so a caller that fails mid-recipe can roll
    back exactly the creatures this call created — never one a concurrent
    task added meanwhile.
    """
    config = _resolve_recipe(recipe)
    logical_names = _validate_logical_names(config)
    declared_aliases = _validate_declared_name_aliases(config)
    builder = creature_builder or build_creature
    use_default_builder = creature_builder is None
    graph_lock = None
    if graph is not None:
        graph_id = engine._resolve_graph_id(graph)
        graph_lock = engine._recipe_graph_locks.setdefault(graph_id, asyncio.Lock())
    graph_cm = graph_lock if graph_lock is not None else nullcontext()

    async with graph_cm:
        if graph is not None:
            graph_id = engine._resolve_graph_id(graph)
            for name in declared_aliases:
                ensure_graph_name_available(
                    engine._topology,
                    engine._creatures,
                    graph_id=graph_id,
                    name=name,
                )
        async with engine._recipe_identities.reserve(
            engine, logical_names
        ) as runtime_ids:
            return await apply_resolved_recipe(
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
                created_ids=created_ids,
                transaction=transaction,
            )


def _validate_logical_names(config: TerrariumConfig) -> list[str]:
    """Return recipe logical names, rejecting names that make wiring ambiguous."""
    names = [creature.name for creature in config.creatures]
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        formatted = ", ".join(repr(name) for name in duplicates)
        raise ValueError(
            f"Terrarium recipe contains duplicate logical creature name(s): "
            f"{formatted}. Logical creature names must be unique within one recipe."
        )
    if config.root is not None:
        if "root" in names:
            raise ValueError(
                "Terrarium recipe cannot declare both a root section and a regular "
                "creature named 'root'"
            )
        names.append("root")
    return names


def _validate_declared_name_aliases(config: TerrariumConfig) -> list[str]:
    """Return every declared graph alias, rejecting cross-member ambiguity."""
    owners: dict[str, int] = {}
    duplicates: set[str] = set()
    for owner, creature in enumerate(config.creatures):
        aliases = {creature.name}
        configured_name = creature.config_data.get("name")
        if isinstance(configured_name, str) and configured_name:
            aliases.add(configured_name)
        for alias in aliases:
            previous_owner = owners.setdefault(alias, owner)
            if previous_owner != owner:
                duplicates.add(alias)

    if config.root is not None:
        root_owner = len(config.creatures)
        previous_owner = owners.setdefault("root", root_owner)
        if previous_owner != root_owner:
            duplicates.add("root")

    if duplicates:
        formatted = ", ".join(repr(name) for name in sorted(duplicates))
        raise ValueError(
            "Terrarium recipe contains duplicate creature name alias(es): "
            f"{formatted}. Display and configured names must be unique within "
            "one graph."
        )
    return sorted(owners)


def _build_recipe_creature(
    builder: CreatureBuilder,
    cfg: CreatureConfig,
    *,
    creature_id: str,
    pwd: str | None,
    llm: Any,
    env: Environment,
    use_default_builder: bool,
    strict: bool = True,
) -> Creature:
    if use_default_builder:
        return builder(
            cfg,
            creature_id=creature_id,
            pwd=pwd,
            llm=llm,
            environment=env,
            strict=strict,
        )
    creature = builder(cfg, creature_id=creature_id, pwd=pwd)
    creature.agent.environment = env
    if getattr(creature.agent, "executor", None) is not None:
        creature.agent.executor._environment = env
    return creature
