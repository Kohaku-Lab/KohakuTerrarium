"""Recipe loader — apply a ``TerrariumConfig`` to a Terrarium engine.

A recipe is just a YAML / dataclass description of "add these creatures,
declare these channels, wire these listen/send edges."  The engine has
all the primitives needed; this file is the thin glue that walks a
recipe and calls them in dependency order.

Auto-created channels (per legacy behaviour):

- One queue channel named after each creature — the "direct" channel
  any other creature can address.
- ``report_to_root`` queue channel when the recipe declares a root.

The root-agent itself (with terrarium-management tools force-registered)
is wired up by the higher-level entry points; this loader marks it via
``Creature.config`` but doesn't bind tools.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Callable

import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.topology as _topo
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.terrarium.config import (
    TerrariumConfig,
    load_terrarium_config,
)
from kohakuterrarium.terrarium.creature_host import (
    Creature,
    build_creature,
)
from kohakuterrarium.terrarium.topology import ChannelKind, GraphTopology
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)


CreatureBuilder = Callable[..., Creature]


def _resolve_recipe(
    recipe: TerrariumConfig | str | Path,
) -> TerrariumConfig:
    if isinstance(recipe, TerrariumConfig):
        return recipe
    return load_terrarium_config(recipe)


def _kind_from_string(s: str) -> ChannelKind:
    return ChannelKind.BROADCAST if s == "broadcast" else ChannelKind.QUEUE


async def apply_recipe(
    engine: "Terrarium",
    recipe: TerrariumConfig | str | Path,
    *,
    graph: GraphTopology | str | None = None,
    pwd: str | None = None,
    creature_builder: CreatureBuilder | None = None,
) -> GraphTopology:
    """Load a terrarium recipe into ``engine`` and return the resulting
    :class:`GraphTopology`.

    All creatures land in a single graph (created fresh when ``graph``
    is None).  ``creature_builder`` defaults to
    :func:`terrarium.creature_host.build_creature`; tests pass a stub
    that returns fake-Agent creatures.
    """
    config = _resolve_recipe(recipe)
    builder = creature_builder or build_creature

    # 1. Mint or reuse the graph by adding the first creature.
    if graph is not None:
        graph_id = engine._resolve_graph_id(graph)
    elif not config.creatures:
        # Empty recipe — create a fresh empty graph directly so the
        # return shape stays consistent.
        graph_id = _topo.new_graph_id()
        engine._topology.graphs[graph_id] = _topo.GraphTopology(graph_id=graph_id)
        engine._environments[graph_id] = Environment(env_id=f"env_{graph_id}")
    else:
        first_cfg = config.creatures[0]
        first_creature = builder(
            first_cfg,
            creature_id=first_cfg.name,
            pwd=pwd,
        )
        first = await engine.add_creature(first_creature, start=False)
        graph_id = first.graph_id

    # 2. Pre-declare every channel the recipe wants.
    for ch_cfg in config.channels:
        await engine.add_channel(
            graph_id,
            ch_cfg.name,
            kind=_kind_from_string(ch_cfg.channel_type),
            description=ch_cfg.description,
        )
        logger.debug("Recipe channel declared", channel=ch_cfg.name)

    # 3. Auto-direct channels (one queue per creature) — added even for
    #    creatures the recipe didn't list as having explicit inbound.
    for cr_cfg in config.creatures:
        if cr_cfg.name not in engine.get_graph(graph_id).channels:
            await engine.add_channel(
                graph_id,
                cr_cfg.name,
                kind=ChannelKind.QUEUE,
                description=f"Direct channel to {cr_cfg.name}",
            )

    # 4. report_to_root when a root is declared.
    has_root = config.root is not None
    if has_root and "report_to_root" not in engine.get_graph(graph_id).channels:
        await engine.add_channel(
            graph_id,
            "report_to_root",
            kind=ChannelKind.QUEUE,
            description="Any creature can report to the root agent",
        )

    # 5. Add the remaining creatures.
    for cr_cfg in config.creatures[1:]:
        creature = builder(
            cr_cfg,
            creature_id=cr_cfg.name,
            pwd=pwd,
        )
        await engine.add_creature(creature, graph=graph_id, start=False)

    # 6. Wire listen/send edges + inject triggers.
    env = engine._environments[graph_id]
    for cr_cfg in config.creatures:
        creature = engine.get_creature(cr_cfg.name)
        # Always listen to the creature's own direct channel.
        all_listen = list(cr_cfg.listen_channels)
        if cr_cfg.name not in all_listen:
            all_listen.append(cr_cfg.name)
        for ch in all_listen:
            try:
                _topo.set_listen(
                    engine._topology,
                    creature.creature_id,
                    ch,
                    listening=True,
                )
            except KeyError:
                # Channel not declared — recipe-author error; skip
                # silently (parity with legacy behaviour).
                continue
            _channels.inject_channel_trigger(
                creature.agent,
                subscriber_id=cr_cfg.name,
                channel_name=ch,
                registry=env.shared_channels,
                ignore_sender=cr_cfg.name,
            )
            if ch not in creature.listen_channels:
                creature.listen_channels.append(ch)
        # send edges — no trigger needed; the agent emits to the channel
        # via ``send_message`` tool, which uses the registry directly.
        all_send = list(cr_cfg.send_channels)
        if has_root and "report_to_root" not in all_send:
            all_send.append("report_to_root")
        for ch in all_send:
            try:
                _topo.set_send(
                    engine._topology,
                    creature.creature_id,
                    ch,
                    sending=True,
                )
            except KeyError:
                continue
            if ch not in creature.send_channels:
                creature.send_channels.append(ch)

    # 7. Start every creature now that wiring is complete.
    for cr_cfg in config.creatures:
        creature = engine.get_creature(cr_cfg.name)
        await creature.start()

    logger.info(
        "Recipe applied",
        terrarium=config.name,
        creatures=len(config.creatures),
        channels=len(config.channels),
        root=has_root,
    )
    return engine.get_graph(graph_id)
