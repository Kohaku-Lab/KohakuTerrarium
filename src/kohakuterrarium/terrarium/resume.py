"""Engine-level resume — adopt a saved session into a live engine.

Resume is an engine concern: rebuild creatures from saved config,
inject the saved conversation / scratchpad / triggers / events, wrap
each agent in a :class:`Creature`, attach the :class:`SessionStore`
at the graph level, and start everything.  The Studio tier sits on
top of this and only adds metadata bookkeeping (``_meta`` /
``_session_stores`` in :mod:`studio.sessions.lifecycle`) plus the
HTTP / CLI orchestration.
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kohakuterrarium.errors import (
    GraphManifestCollisionError,
    SessionNotResumableError,
)
from kohakuterrarium.builtins.inputs.none import NoneInput
from kohakuterrarium.core.config_serde import pack_agent_config
from kohakuterrarium.session.resume import (
    _open_store_with_migration,
    detect_session_type,
    inject_saved_state,
    resume_agent,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium import channels as _channels
from kohakuterrarium.terrarium import graph_checkpoint as _checkpoint
from kohakuterrarium.terrarium import graph_manifest as _manifest
from kohakuterrarium.terrarium import topology as _topology
from kohakuterrarium.terrarium.config import load_terrarium_config
from kohakuterrarium.terrarium.creature_host import (
    Creature,
    _safe_creature_id,
    apply_creature_name,
)
import kohakuterrarium.terrarium.topology_snapshot as _topo_snap
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import Terrarium

logger = get_logger(__name__)


def _schedule_drive_reconcile(engine: "Terrarium", creature: "Creature") -> None:
    """Arm Drive reconciliation for a resumed creature.

    Resume starts creatures directly (not via ``engine.add_creature``), so it
    must wire the restoration-barrier-gated reconcile itself. A no-op on a
    Drive-disabled engine."""
    runtime = getattr(engine, "_drive_runtime", None)
    if runtime is not None:
        runtime.schedule_reconcile(creature)


async def resume_into_engine(
    engine: "Terrarium",
    store: SessionStore | str | Path,
    *,
    pwd: str | None = None,
    llm: Any = None,
) -> str:
    """Adopt a saved session into ``engine``.  Returns the graph_id.

    Reads the saved store's metadata to dispatch between the agent
    and terrarium rebuild paths, wraps the rebuilt agent(s) in
    :class:`Creature` objects, adopts them via ``engine.add_creature``,
    and attaches the ``SessionStore`` at the graph level.

    ``store`` may be a path-like to a ``.kohakutr`` file or an
    already-open :class:`SessionStore` instance.  An instance is CLOSED
    here first — the rebuild paths open the file themselves, and two
    live handles on one session file keep independent event counters.
    """
    path = _resolve_store_path(store)
    if isinstance(store, SessionStore):
        store.close(update_status=False)
    probe = _open_store_with_migration(path)
    meta = probe.load_meta()
    if _manifest.MANIFEST_KEY in meta:
        _manifest.parse_manifest(meta[_manifest.MANIFEST_KEY])
        close = getattr(probe, "close", None)
        if close is not None:
            close(update_status=False)
        locked = _open_store_with_migration(path, writer_lock=True)
        try:
            locked_manifest = _manifest.parse_manifest(
                locked.load_meta()[_manifest.MANIFEST_KEY]
            )
        except BaseException:
            locked.close(update_status=False)
            raise
        return await _resume_manifest_into_engine(
            engine,
            path,
            locked_manifest,
            pwd=pwd,
            llm=llm,
            store=locked,
        )
    close = getattr(probe, "close", None)
    if close is not None:
        close(update_status=False)
    session_type = detect_session_type(path)

    if session_type == "agent":
        return await _resume_agent_into_engine(engine, path, pwd=pwd, llm=llm)
    if session_type == "terrarium":
        return await _resume_terrarium_into_engine(engine, path, pwd=pwd, llm=llm)
    raise SessionNotResumableError(f"Unknown saved-session type: {session_type!r}")


def _resolve_store_path(store: SessionStore | str | Path) -> Path:
    if isinstance(store, SessionStore):
        # SessionStore exposes ``path`` (set in __init__) — fall back
        # to ``str(store)`` only if the attribute is missing on a future
        # store implementation.
        return Path(getattr(store, "path", str(store)))
    return Path(str(store))


async def _resume_manifest_into_engine(
    engine: "Terrarium",
    path: Path,
    manifest: _manifest.GraphManifest,
    *,
    pwd: str | None,
    llm: Any = None,
    store: SessionStore | None = None,
) -> str:
    """Restore a graph exactly from an authoritative manifest."""
    store = store or _open_store_with_migration(path, writer_lock=True)
    if manifest.graph_id in engine._topology.graphs:
        store.close(update_status=False)
        raise GraphManifestCollisionError("graph_id", manifest.graph_id)
    for item in manifest.creatures:
        if (
            item.creature_id in engine._creatures
            or item.creature_id in engine._topology.creature_to_graph
        ):
            store.close(update_status=False)
            raise GraphManifestCollisionError("creature_id", item.creature_id)

    created: list[Creature] = []
    attached = False
    engine._create_restore_graph(manifest.graph_id)
    try:
        for item in manifest.creatures:
            creature = await engine.add_creature(
                _manifest.unpack_creature_config(item),
                llm=llm,
                pwd=pwd or item.pwd,
                io="none",
                strict=False,
                session=False,
                start=False,
                graph=manifest.graph_id,
                creature_id=item.creature_id,
                name=item.name,
                is_privileged=item.is_privileged,
                parent_creature_id=item.parent_creature_id,
            )
            creature.injected_runtime = ()
            created.append(creature)

        graph = engine._topology.graphs[manifest.graph_id]
        environment = engine._environments[manifest.graph_id]
        for channel in manifest.channels:
            info = _topology.add_channel(
                engine._topology,
                manifest.graph_id,
                channel.name,
                description=channel.description,
            )
            _channels.register_channel_in_environment(
                environment.shared_channels,
                info,
                engine=engine,
                graph_id=manifest.graph_id,
            )
        for creature_id, channel_name in manifest.listen:
            _topology.set_listen(
                engine._topology, creature_id, channel_name, listening=True
            )
        for creature_id, channel_name in manifest.send:
            _topology.set_send(
                engine._topology, creature_id, channel_name, sending=True
            )

        for creature in created:
            creature.listen_channels = sorted(
                graph.listen_edges.get(creature.creature_id, set())
            )
            creature.send_channels = sorted(
                graph.send_edges.get(creature.creature_id, set())
            )
            _channels.bind_creature_to_environment(creature, environment)
            for channel_name in creature.listen_channels:
                _channels.inject_channel_trigger(
                    creature.agent,
                    subscriber_id=creature.name,
                    channel_name=channel_name,
                    registry=environment.shared_channels,
                    ignore_sender_id=creature.creature_id,
                )

        await engine.attach_session(manifest.graph_id, store)
        attached = True
        engine._owned_sessions.add(manifest.graph_id)
        for creature in created:
            inject_saved_state(creature.agent, store, creature.name)
            apply_creature_name(creature, creature.name)
            await creature.start()
            _schedule_drive_reconcile(engine, creature)
        store.update_status("running")
        await _checkpoint.checkpoint(engine, manifest.graph_id)
        return manifest.graph_id
    except BaseException:
        environment = engine._environments.get(manifest.graph_id)
        for creature in reversed(created):
            try:
                if creature.is_running:
                    await creature.stop()
                registry = getattr(environment, "shared_channels", None)
                for channel_name in creature.listen_channels:
                    channel = (
                        registry.get(channel_name) if registry is not None else None
                    )
                    if channel is not None:
                        channel.unsubscribe(creature.name)
            except BaseException:
                pass
            engine._creatures.pop(creature.creature_id, None)
            getattr(engine, "_runtime_contexts", {}).pop(creature.creature_id, None)
            engine._topology.creature_to_graph.pop(creature.creature_id, None)
        if attached:
            engine._session_stores.pop(manifest.graph_id, None)
            engine._owned_sessions.discard(manifest.graph_id)
        runtime = getattr(engine, "_drive_runtime", None)
        if runtime is not None:
            try:
                await runtime.detach_graph(manifest.graph_id)
            except BaseException:
                pass
        engine._environments.pop(manifest.graph_id, None)
        engine._topology.graphs.pop(manifest.graph_id, None)
        store.close(update_status=False)
        raise


async def _resume_agent_into_engine(
    engine: "Terrarium",
    path: Path,
    *,
    pwd: str | None,
    llm: Any,
) -> str:
    """Standalone-agent resume: rebuild Agent, wrap, adopt, attach.

    The rebuilt agent is adopted into a live engine and driven through
    the engine's wiring / attach WebSocket — never its config's own
    ``input: cli`` loop. ``input_module=NoneInput()`` suppresses that
    loop exactly as ``engine.add_creature(io="none")`` does for
    the Studio / Lab spawn path; without it a worker-side resume boots
    a stdin reader with no TTY and wedges the worker.
    """
    # session.resume.resume_agent does the heavy lifting: opens store
    # with migration, rebuilds Agent from the saved config, injects
    # every state slot, and calls agent.attach_session_store(store).
    # Its own handler closes the store if the rebuild fails; the guard
    # below covers the adopt-into-engine steps that run after it returns.
    agent, store = resume_agent(
        path,
        pwd_override=pwd,
        io_mode=None,
        llm=llm,
        input_module=NoneInput(),
    )
    created: list[str] = []
    try:
        meta = getattr(store, "meta", {})
        snapshot = meta.get("config_snapshot") if hasattr(meta, "get") else None
        if snapshot is None:
            try:
                snapshot = pack_agent_config(agent.config)
            except (AttributeError, TypeError):
                snapshot = {"name": agent.config.name}
        creature_obj = Creature(
            creature_id=_safe_creature_id(agent.config.name),
            name=agent.config.name,
            agent=agent,
            config=agent.config,
            config_snapshot=snapshot,
            source_ref=meta.get("config_path") if hasattr(meta, "get") else None,
            build_pwd=str(
                pwd
                or (meta.get("pwd") if hasattr(meta, "get") else None)
                or os.getcwd()
            ),
        )
        # ``session=False``: the SAVED store attaches below — autosession
        # minting a fresh sibling file here would orphan it on disk.
        # ``start=False``: ``add_creature`` inserts into the topology +
        # ``_creatures`` before awaiting startup, so the id must be
        # recorded at insertion — a start failure would else leave the
        # creature adopted but absent from the rollback list.
        creature = await engine.add_creature(creature_obj, start=False, session=False)
        created.append(creature.creature_id)

        # Attach at graph level. ``Agent.attach_session_store`` is
        # idempotent for the same store, so this updates graph bookkeeping
        # without adding a duplicate SessionOutput sink. Resume OPENED this
        # store — register it as engine-owned so shutdown closes it (a
        # leaked writer lock blocks any later adopt of the same file).
        await engine.attach_session(creature.graph_id, store)
        engine._owned_sessions.add(creature.graph_id)

        await creature.start()
        # The restoration barrier gates Drive reconciliation. The session store
        # and Drive repository are already attached, so persisted Drives
        # redeliver only after startup settles.
        _schedule_drive_reconcile(engine, creature)
        await _checkpoint.checkpoint(engine, creature.graph_id)

        logger.info(
            "Agent session resumed into engine",
            session_id=creature.graph_id,
            creature_id=creature.creature_id,
            path=str(path),
        )
        return creature.graph_id
    except BaseException:
        await _rollback_failed_adoption(engine, store, created)
        raise


async def _resume_terrarium_into_engine(
    engine: "Terrarium",
    path: Path,
    *,
    pwd: str | None,
    llm: Any = None,
) -> str:
    """Multi-creature recipe resume: rebuild graph, inject per-creature."""
    store = _open_store_with_migration(path, writer_lock=True)
    # ``apply_recipe`` appends every creature it adds here, so a failure
    # rolls back exactly this adoption's creatures — never one a
    # concurrent task added meanwhile.
    created: list[str] = []
    try:
        return await _resume_terrarium_body(
            engine, path, store, created, pwd=pwd, llm=llm
        )
    except BaseException:
        await _rollback_failed_adoption(engine, store, created)
        raise


async def _resume_terrarium_body(
    engine: "Terrarium",
    path: Path,
    store: SessionStore,
    created: list[str],
    *,
    pwd: str | None,
    llm: Any = None,
) -> str:
    """Rebuild + rehydrate the terrarium graph from an already-open store.

    Split out of :func:`_resume_terrarium_into_engine` so the caller can
    guard the whole flow with one close-and-rollback handler.  ``created``
    accumulates the ids of the creatures this adoption adds.
    """
    meta = store.load_meta()
    config_path = meta.get("config_path", "")
    if not config_path:
        raise SessionNotResumableError("Saved terrarium has no config_path in metadata")

    # ``pwd`` flows into ``apply_recipe`` for per-creature workspaces;
    # resume must not change the process-wide working directory.
    saved_pwd = meta.get("pwd", ".")
    pwd = pwd or saved_pwd
    if not (pwd and os.path.isdir(pwd)):
        if pwd and pwd == saved_pwd:
            logger.warning(
                "Saved working dir no longer exists; falling back to cwd",
                saved_pwd=pwd,
            )
        pwd = None

    config = load_terrarium_config(config_path)

    # Build the topology via the engine — creates every creature and
    # wires channels, but ``start=False``: a started creature schedules
    # its input drive and startup triggers immediately, which would run
    # against an EMPTY conversation before the saved state lands. The
    # per-creature start happens below, after injection.
    #
    # ``session=False``: the SAVED store attaches below.  Under
    # autosession (``Terrarium(session_dir=...)`` — every API-server
    # engine) ``apply_recipe`` would otherwise mint a fresh
    # ``<new_gid>.kohakutr`` that the saved-store attach immediately
    # orphans: an empty ghost file stuck at ``status="running"`` in
    # the saved-session list, plus a leaked open handle.  Mirrors the
    # ``session=False`` in ``_resume_agent_into_engine``.
    graph = await engine.apply_recipe(
        config, pwd=pwd, llm=llm, session=False, start=False, created_ids=created
    )
    sid = graph.graph_id

    # Per-creature state injection.
    #
    # Resolve each rebuilt creature to its *saved* runtime name. Saved
    # events are keyed by ``<saved_name>:e:<id>``, but ``apply_recipe``
    # may have produced a creature whose current ``config.name`` does
    # not match that key (the fresh build can re-roll random suffixes,
    # or the saved recipe stored an explicit per-creature name that the
    # rebuild collapsed to the config default). Match in this order:
    #
    #   1. fresh name already exists in the saved agents list;
    #   2. otherwise consume the next unused saved name positionally.
    #
    # Without this we'd inject under the fresh name and recover zero
    # events. Same root cause as the creature-resume bug fixed in
    # ``session.resume.align_agent_name``.
    saved_agents = list(meta.get("agents") or [])
    saved_set = set(saved_agents)
    consumed: set[str] = set()
    for cid in graph.creature_ids:
        try:
            creature = engine.get_creature(cid)
        except KeyError:
            continue
        fresh = creature.agent.config.name
        if fresh in saved_set and fresh not in consumed:
            agent_name = fresh
        else:
            # Pull the next saved name we haven't consumed yet.
            agent_name = next(
                (n for n in saved_agents if n not in consumed),
                fresh,
            )
        consumed.add(agent_name)
        inject_saved_state(creature.agent, store, agent_name)
        # ``inject_saved_state`` aligns ``agent.config.name``; mirror
        # the saved name onto the Creature wrapper too so chat-history
        # lookups (which key off ``creature.name``) hit the same
        # namespace as the events we just injected.
        creature.name = agent_name
        if creature.config is not None:
            creature.config.name = agent_name
        creature.agent.attach_session_store(store)

    # Attach at graph level. Each creature was already attached just
    # above, but ``Agent.attach_session_store`` is idempotent for the
    # same store so this preserves graph/session bookkeeping safely.
    # Must precede the topology replay — the replay reads
    # ``runtime_topology`` off the engine-attached store. Resume OPENED
    # this store — register it as engine-owned so shutdown closes it.
    await engine.attach_session(sid, store)
    engine._owned_sessions.add(sid)
    store.update_status("running")

    # Replay runtime topology mutations on top of the recipe-rebuilt
    # graph BEFORE anything starts: any channel the user added via
    # ``service.add_channel`` / any wire from ``service.connect`` after
    # the original spawn lives in ``meta["runtime_topology"]``. A
    # startup trigger must see the restored channels + wires, not the
    # bare recipe topology.
    await _topo_snap.replay(engine, sid)

    # Saved state, session, and topology are in — NOW the creatures may
    # start (startup triggers and inbound input see the restored
    # conversation and graph, not the bare recipe).
    for cid in graph.creature_ids:
        try:
            creature = engine.get_creature(cid)
        except KeyError:
            continue
        await creature.start()
        # The restoration barrier gates Drive reconciliation. The session and
        # Drive repository are attached above, so persisted Drives redeliver
        # only once each creature's startup settles.
        _schedule_drive_reconcile(engine, creature)

    if sid in engine._topology.graphs:
        await _checkpoint.checkpoint(engine, sid)
    logger.info(
        "Terrarium session resumed into engine",
        session_id=sid,
        path=str(path),
        creatures=len(graph.creature_ids),
    )
    return sid


async def _rollback_failed_adoption(
    engine: "Terrarium",
    store: SessionStore,
    created_ids: list[str],
) -> None:
    """Best-effort undo of a partial session adoption.

    Detaches ``store`` from the engine by identity FIRST so the creature
    removals below don't run split coordination against it, removes only
    the creatures this adoption created (by exact id, so a creature a
    concurrent task added mid-adoption is never touched), then closes the
    store to release its writer lock.  Pre-existing graphs are left
    untouched.
    """
    for gid, s in list(engine._session_stores.items()):
        if s is store:
            engine._session_stores.pop(gid, None)
            engine._owned_sessions.discard(gid)
    for cid in created_ids:
        if cid not in engine._creatures:
            continue
        try:
            await engine.remove_creature(cid)
        except Exception:
            logger.warning(
                "resume rollback: remove_creature failed",
                creature_id=cid,
                exc_info=True,
            )
    try:
        store.close(update_status=False)
    except Exception:
        logger.warning("resume rollback: store close failed", exc_info=True)
