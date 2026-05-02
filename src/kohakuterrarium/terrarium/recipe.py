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
    CreatureConfig,
    TerrariumConfig,
    load_terrarium_config,
)
from kohakuterrarium.terrarium.creature_host import Creature, build_creature
from kohakuterrarium.terrarium.factory import (
    build_root_awareness_prompt,
    force_register_terrarium_tools,
    inject_prompt_section,
)
import kohakuterrarium.terrarium.wiring as _wiring
from kohakuterrarium.terrarium.tool_manager import (
    TERRARIUM_MANAGER_KEY,
    TerrariumToolManager,
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
    llm_override: str | None = None,
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
    use_default_builder = creature_builder is None

    # 1. Mint or reuse the graph and its shared environment before building
    # agents, so recipe-created agents receive the graph Environment in their
    # ToolContext and can use shared channels.
    if graph is not None:
        graph_id = engine._resolve_graph_id(graph)
    else:
        graph_id = _topo.new_graph_id()
        engine._topology.graphs[graph_id] = _topo.GraphTopology(graph_id=graph_id)
        engine._environments[graph_id] = Environment(env_id=f"env_{graph_id}")
    env = engine._environments[graph_id]

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

    # 5. Add every configured creature.
    for cr_cfg in config.creatures:
        creature = _build_recipe_creature(
            builder,
            cr_cfg,
            creature_id=cr_cfg.name,
            pwd=pwd,
            llm_override=llm_override,
            env=env,
            use_default_builder=use_default_builder,
        )
        await engine.add_creature(creature, graph=graph_id, start=False)

    root_creature: Creature | None = None
    if config.root is not None:
        root_data = dict(config.root.config_data)
        root_data["name"] = "root"
        root_cfg = CreatureConfig(
            name="root",
            config_data=root_data,
            base_dir=config.root.base_dir,
        )
        root_creature = _build_recipe_creature(
            builder,
            root_cfg,
            creature_id="root",
            pwd=pwd,
            llm_override=llm_override,
            env=env,
            use_default_builder=use_default_builder,
        )
        await engine.add_creature(root_creature, graph=graph_id, start=False)
        _prepare_root_creature(engine, config, graph_id, root_creature)

    # 6. Wire listen/send edges + inject triggers.
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

    if root_creature is not None:
        await engine.assign_root(root_creature)

    _install_recipe_output_wiring_resolver(engine, graph_id, root_creature)

    # 7. Start every creature now that wiring is complete.
    for cid in list(engine.get_graph(graph_id).creature_ids):
        creature = engine.get_creature(cid)
        await creature.start()

    logger.info(
        "Recipe applied",
        terrarium=config.name,
        creatures=len(config.creatures),
        channels=len(config.channels),
        root=has_root,
    )
    return engine.get_graph(graph_id)


def _build_recipe_creature(
    builder: CreatureBuilder,
    cfg: CreatureConfig,
    *,
    creature_id: str,
    pwd: str | None,
    llm_override: str | None,
    env: Environment,
    use_default_builder: bool,
) -> Creature:
    if use_default_builder:
        return builder(
            cfg,
            creature_id=creature_id,
            pwd=pwd,
            llm_override=llm_override,
            environment=env,
        )
    creature = builder(cfg, creature_id=creature_id, pwd=pwd)
    creature.agent.environment = env
    if getattr(creature.agent, "executor", None) is not None:
        creature.agent.executor._environment = env
    return creature


def _prepare_root_creature(
    engine: "Terrarium",
    config: TerrariumConfig,
    graph_id: str,
    root: Creature,
) -> None:
    registry = getattr(root.agent, "registry", None)
    if registry is not None and hasattr(registry, "register_tool"):
        force_register_terrarium_tools(root.agent)
    if getattr(root.agent, "controller", None) is not None:
        inject_prompt_section(root.agent, build_root_awareness_prompt(config))
    manager = TerrariumToolManager()
    manager.register_runtime(
        config.name,
        _EngineRuntimeAdapter(engine, graph_id, config.name),
    )
    root.agent.environment.register(TERRARIUM_MANAGER_KEY, manager)


class _EngineRuntimeAdapter:
    def __init__(self, engine: "Terrarium", graph_id: str, name: str) -> None:
        self.engine = engine
        self.graph_id = graph_id
        self.name = name
        self.environment = engine._environments[graph_id]

    def get_status(self) -> dict:
        graph = self.engine.get_graph(self.graph_id)
        return {
            "name": self.name,
            "running": True,
            "has_root": any(
                getattr(self.engine.get_creature(cid), "is_root", False)
                for cid in graph.creature_ids
            ),
            "creatures": {
                cid: self.engine.get_creature(cid).get_status()
                for cid in graph.creature_ids
            },
            "channels": self.environment.shared_channels.get_channel_info(),
        }

    async def stop(self) -> None:
        await self.engine.stop_graph(self.graph_id)

    async def add_creature(self, creature_cfg: CreatureConfig) -> Creature:
        return await self.engine.add_creature(creature_cfg, graph=self.graph_id)

    async def remove_creature(self, name: str) -> bool:
        try:
            creature = self.engine.get_creature(name)
        except KeyError:
            return False
        if creature.graph_id != self.graph_id:
            return False
        await self.engine.remove_creature(name)
        return True

    def get_creature_agent(self, name: str):
        try:
            creature = self.engine.get_creature(name)
        except KeyError:
            return None
        if creature.graph_id != self.graph_id:
            return None
        return creature.agent


def _install_recipe_output_wiring_resolver(
    engine: "Terrarium", graph_id: str, root_creature: Creature | None
) -> None:
    _wiring.install_output_wiring_resolver(engine)
