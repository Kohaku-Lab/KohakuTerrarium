"""Creature construction and insertion helpers for the Terrarium engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import kohakuterrarium.terrarium.autosession as _autosession
import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.graph_checkpoint as _checkpoint
import kohakuterrarium.terrarium.graph_identity_engine as _identity
import kohakuterrarium.terrarium.topology as _topo
import kohakuterrarium.terrarium.wiring as _wiring
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.creature_host import (
    Creature,
    CreatureBuildInput,
    apply_creature_name,
    build_creature,
)
from kohakuterrarium.terrarium.events import EngineEvent, EventKind

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium
    from kohakuterrarium.terrarium.topology import GraphTopology


async def add_creature(
    engine: Terrarium,
    config: CreatureBuildInput | Creature,
    *,
    graph: GraphTopology | str | None = None,
    creature_id: str | None = None,
    llm: Any = None,
    pwd: str | None = None,
    start: bool = True,
    is_privileged: bool = False,
    parent_creature_id: str | None = None,
    io: str = "config",
    strict: bool = True,
    session: bool | str | Path | SessionStore | None = None,
    name: str | None = None,
    tools: list[Any] | None = None,
    plugins: list[Any] | None = None,
    builder=build_creature,
    register_basic=None,
    register_privileged=None,
    identity_reserved: bool = False,
) -> Creature:
    """Build, insert, optionally start, persist, and checkpoint one creature."""
    creature = _prepare_creature(
        engine,
        config,
        creature_id=creature_id,
        llm=llm,
        pwd=pwd,
        io=io,
        strict=strict,
        name=name,
        tools=tools,
        plugins=plugins,
        builder=builder,
    )
    _identity.bind_runtime_creature_id(creature)
    claimed_identity = False
    if not identity_reserved:
        claimed_identity = await engine._recipe_identities.claim_exact(
            engine, creature.creature_id
        )
        if not claimed_identity:
            raise ValueError(f"creature_id {creature.creature_id!r} already exists")
    elif creature.creature_id in engine._creatures:
        raise ValueError(f"creature_id {creature.creature_id!r} already exists")

    graph_id = engine._resolve_graph_id(graph) if graph is not None else None
    try:
        _identity.guard_add_name(engine, creature, graph_id)
        if graph_id is not None:
            await _checkpoint.preflight_add(
                engine,
                graph_id,
                creature,
                will_persist=session is not False
                and (session is not None or engine._session_dir is not None),
            )
        gid = _topo.add_creature(
            engine._topology, creature.creature_id, graph_id=graph_id
        )
    except BaseException:
        if claimed_identity:
            await engine._recipe_identities.release_exact(creature.creature_id)
        raise
    if claimed_identity:
        await engine._recipe_identities.release_exact(creature.creature_id)
    creature.graph_id = gid
    if is_privileged:
        creature.is_privileged = True
    if parent_creature_id is not None:
        creature.parent_creature_id = parent_creature_id
    if gid not in engine._environments:
        engine._environments[gid] = Environment(env_id=f"env_{gid}")
    graph_env = engine._environments[gid]
    _channels.bind_creature_to_environment(creature, graph_env)
    _channels.register_engine_handle(graph_env, engine)
    engine._creatures[creature.creature_id] = creature
    _wiring.install_output_wiring_resolver(engine)

    if register_basic is not None:
        register_basic(creature.agent)
    if creature.is_privileged and register_privileged is not None:
        register_privileged(creature.agent)
    if engine._drive_runtime is not None:
        await engine._drive_runtime.attach_creature(creature, graph_env)

    engine._emit(
        EngineEvent(
            kind=EventKind.CREATURE_ADDED,
            creature_id=creature.creature_id,
            graph_id=gid,
        )
    )
    try:
        if start:
            await creature.start()
        await _autosession.attach_for_new_creature(
            engine, creature, config=config, session=session
        )
        if start:
            engine._emit(
                EngineEvent(
                    kind=EventKind.CREATURE_STARTED,
                    creature_id=creature.creature_id,
                    graph_id=gid,
                )
            )
            if engine._drive_runtime is not None:
                engine._drive_runtime.schedule_reconcile(creature)
        await _checkpoint.checkpoint(engine, gid)
        return creature
    except BaseException:
        cleanup = asyncio.create_task(engine.remove_creature(creature.creature_id))
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise
        raise


def _prepare_creature(
    engine: Terrarium,
    config: CreatureBuildInput | Creature,
    *,
    creature_id: str | None,
    llm: Any,
    pwd: str | None,
    io: str,
    strict: bool,
    name: str | None,
    tools: list[Any] | None,
    plugins: list[Any] | None,
    builder,
) -> Creature:
    if isinstance(config, Creature):
        ignored = [
            key
            for key, value in (
                ("llm", llm),
                ("pwd", pwd),
                ("io", io),
                ("tools", tools),
                ("plugins", plugins),
            )
            if value not in (None, "config")
        ]
        if ignored:
            raise ValueError(
                "add_creature received a pre-built Creature; build-time "
                f"argument(s) {', '.join(ignored)} cannot be applied. Pass them "
                "to build_creature / the config-based overload."
            )
        creature = config
    else:
        creature = builder(
            config,
            creature_id=creature_id,
            pwd=pwd if pwd is not None else engine._pwd,
            llm=llm,
            io=io,
            strict=strict,
            tools=tools,
            plugins=plugins,
        )
    if creature_id and creature.creature_id != creature_id:
        creature.creature_id = creature_id
    if name and name.strip():
        apply_creature_name(creature, name.strip())
    return creature
