"""Channel layer for the Terrarium engine.

Bridges the pure-data topology (``terrarium.topology``) and the live
``Environment.shared_channels`` registry from ``core.channel``.  Owns
channel injection — when a creature joins a graph that has channels it
listens to, a :class:`ChannelTrigger` is added to its
``trigger_manager``.

Supports both static wiring (declared at recipe-load time) and live
hot-plug (creatures connecting after they're already running).

The ``connect_creatures`` / ``disconnect_creatures`` helpers below are
the bodies of ``Terrarium.connect`` / ``Terrarium.disconnect``;
they're kept here to keep ``engine.py`` under the 600-line cap and
because every line of logic in them is channel-related.
"""

import asyncio
import time
import weakref
from datetime import datetime
from typing import TYPE_CHECKING, Any

import kohakuterrarium.terrarium.session_coord as _session_coord
import kohakuterrarium.terrarium.topology as _topo
from kohakuterrarium.core.channel import ChannelRegistry
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.modules.trigger.channel import ChannelTrigger
from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    EngineEvent,
    EventKind,
)
from kohakuterrarium.terrarium.topology import ChannelInfo
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.engine import (
        CreatureRef,
        Terrarium,
    )

logger = get_logger(__name__)


# Environment registration key for the live engine weakref. Group tools
# read it via ``ToolContext.environment.get(TERRARIUM_ENGINE_KEY)`` to
# resolve the engine without a global singleton.
TERRARIUM_ENGINE_KEY = "terrarium_engine"


def register_channel_in_environment(
    registry: ChannelRegistry,
    info: ChannelInfo,
    *,
    maxsize: int = 0,
    engine: "Terrarium | None" = None,
    graph_id: str | None = None,
) -> Any:
    """Ensure the channel exists in the live ``ChannelRegistry``.

    Graph topology channels are always broadcast. When ``engine`` +
    ``graph_id`` are supplied, also installs the persistence callback
    that writes sends to the graph's session store.
    """
    channel = registry.get_or_create(
        info.name,
        channel_type="broadcast",
        maxsize=maxsize,
        description=info.description,
    )
    if engine is not None and graph_id is not None:
        _ensure_channel_persistence(channel, engine, graph_id)
    return channel


def _ensure_channel_persistence(
    channel: Any, engine: "Terrarium", graph_id: str
) -> None:
    """Idempotent on_send → ``store.save_channel_message`` install.

    ``graph_id`` refreshed on every call so merges that move the
    channel home land in the surviving store. Store resolved at call
    time, not at install time.
    """
    channel._terrarium_graph_id = graph_id
    if getattr(channel, "_terrarium_persistence_installed", False):
        return
    engine_ref = weakref.ref(engine)

    def _persist(channel_name: str, message: Any) -> None:
        gid = getattr(channel, "_terrarium_graph_id", None)
        if not gid:
            return
        live = engine_ref()
        if live is None:
            return
        store = getattr(live, "_session_stores", {}).get(gid)
        if store is None:
            return
        try:
            ts_attr = getattr(message, "timestamp", None)
            if hasattr(ts_attr, "timestamp"):
                ts = ts_attr.timestamp()
            else:
                ts = time.time()
            content = message.content
            if not isinstance(content, (str, list, dict)):
                content = str(content)
            store.save_channel_message(
                channel_name,
                {
                    "sender": getattr(message, "sender", ""),
                    "sender_id": getattr(message, "sender_id", None),
                    "content": content,
                    "message_id": getattr(message, "message_id", ""),
                    "metadata": dict(getattr(message, "metadata", None) or {}),
                    "reply_to": getattr(message, "reply_to", None),
                    "ts": ts,
                },
            )
        except Exception as exc:
            logger.debug(
                "channel persistence failed",
                channel=channel_name,
                error=str(exc),
                exc_info=True,
            )

    channel.on_send(_persist)
    channel._terrarium_persistence_installed = True


def bind_creature_to_environment(creature: Any, env: Environment) -> None:
    """Repoint a creature's agent + executor at ``env``.

    Called from :meth:`Terrarium.add_creature` (when the creature joins
    an existing graph) and from :func:`_merge_environment_into` (when
    two graphs merge). Without this, the creature's
    ``ToolContext.environment.shared_channels`` points at the env it was
    *built* with — which may be empty or different from the surviving
    graph's env.
    """
    if getattr(creature.agent, "environment", None) is not env:
        creature.agent.environment = env
    executor = getattr(creature.agent, "executor", None)
    if executor is not None and getattr(executor, "_environment", None) is not env:
        executor._environment = env


def register_engine_handle(env: Environment, engine: "Terrarium") -> None:
    """Register a weakref to ``engine`` on ``env`` under
    :data:`TERRARIUM_ENGINE_KEY`. Idempotent — overwrites any prior
    registration with a fresh weakref."""
    env.register(TERRARIUM_ENGINE_KEY, weakref.ref(engine))


def inject_channel_trigger(
    agent: Any,
    *,
    subscriber_id: str,
    channel_name: str,
    registry: ChannelRegistry,
    prompt: str | None = None,
    ignore_sender: str | None = None,
    ignore_sender_id: str | None = None,
) -> str:
    """Add a :class:`ChannelTrigger` to ``agent.trigger_manager`` and
    actually start it so the receiver wakes up on each channel send.

    Idempotent — re-injecting an existing trigger tears the old one
    down first so we don't leak run-loop tasks or end up with two
    triggers fighting over the same subscription.

    Important: the trigger MUST be started + driven by the manager's
    run loop, otherwise it sits in ``_triggers`` looking installed
    but ``wait_for_trigger`` never gets called and the creature never
    receives a single message. The original implementation only did
    the dict assignment, which is the root cause of every "I wired
    listen but the creature still hears nothing" report.

    ``ignore_sender_id`` is the stable creature_id form of the
    self-filter — robust against display-name collisions (two creatures
    spawned from the same config sharing one name). Falls back to the
    name-based ``ignore_sender`` filter when not provided.
    """
    prompt = prompt or "[Channel '{channel}' from {sender}]: {content}"
    if ignore_sender_id is None:
        # ``install_output_wiring_resolver`` stamps ``_creature_id`` on
        # the agent at wiring time; fall back to that so we don't need
        # every caller to thread the id through.
        ignore_sender_id = getattr(agent, "_creature_id", None) or getattr(
            agent, "creature_id", None
        )
    trigger = ChannelTrigger(
        channel_name=channel_name,
        subscriber_id=f"{subscriber_id}_{channel_name}",
        prompt=prompt,
        ignore_sender=ignore_sender or subscriber_id,
        ignore_sender_id=ignore_sender_id,
        registry=registry,
    )
    trigger_id = f"channel_{subscriber_id}_{channel_name}"

    _teardown_existing_trigger(agent, trigger_id)
    agent.trigger_manager._triggers[trigger_id] = trigger
    agent.trigger_manager._created_at[trigger_id] = datetime.now()
    _spawn_trigger_runner(agent, trigger_id, trigger)
    logger.debug(
        "Injected channel trigger",
        subscriber=subscriber_id,
        channel=channel_name,
        trigger_id=trigger_id,
    )
    return trigger_id


def remove_channel_trigger(
    agent: Any,
    *,
    subscriber_id: str,
    channel_name: str,
) -> bool:
    """Remove a previously-injected trigger.  Returns True if removed.

    Cancels the trigger's run-loop task and schedules its
    ``stop()`` (which unsubscribes from the channel) so we don't leak
    background tasks each time the user toggles a wire.
    """
    trigger_id = f"channel_{subscriber_id}_{channel_name}"
    if trigger_id not in agent.trigger_manager._triggers:
        return False
    _teardown_existing_trigger(agent, trigger_id)
    agent.trigger_manager._created_at.pop(trigger_id, None)
    logger.debug(
        "Removed channel trigger",
        subscriber=subscriber_id,
        channel=channel_name,
        trigger_id=trigger_id,
    )
    return True


def _teardown_existing_trigger(agent: Any, trigger_id: str) -> None:
    """Stop + remove a trigger if present. Safe to call when there is
    no event loop running (pre-start wiring) and against minimal test
    fakes that don't expose ``_tasks``/``stop()``.
    """
    manager = getattr(agent, "trigger_manager", None)
    if manager is None:
        return
    triggers = getattr(manager, "_triggers", None)
    if triggers is None:
        return
    existing = triggers.pop(trigger_id, None)
    if existing is None:
        return
    tasks = getattr(manager, "_tasks", None)
    if isinstance(tasks, dict):
        task = tasks.pop(trigger_id, None)
        if task is not None and not task.done():
            task.cancel()
    stop = getattr(existing, "stop", None)
    if callable(stop):
        try:
            asyncio.get_running_loop()
            asyncio.create_task(stop())
        except RuntimeError:
            # No loop — pre-start path; subscriptions weren't created yet.
            pass


def _spawn_trigger_runner(agent: Any, trigger_id: str, trigger: Any) -> None:
    """Start the trigger and attach its run-loop task to the manager.

    No-op when called outside an asyncio loop or against a manager
    fake that doesn't implement ``_run_loop`` / ``_tasks`` — the
    real agent's ``start_all`` picks the trigger up later, and tests
    that don't care about runtime delivery don't need this branch.
    """
    manager = getattr(agent, "trigger_manager", None)
    if manager is None:
        return
    if not hasattr(manager, "_run_loop") or not isinstance(
        getattr(manager, "_tasks", None), dict
    ):
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run() -> None:
        start = getattr(trigger, "start", None)
        if callable(start):
            await start()
        # Don't double-spawn if a previous run loop is still pending
        # (e.g. teardown raced with a fresh inject).
        if trigger_id in manager._tasks and not manager._tasks[trigger_id].done():
            return
        manager._tasks[trigger_id] = asyncio.create_task(
            manager._run_loop(trigger_id, trigger),
            name=f"trigger_{trigger_id}",
        )

    asyncio.create_task(_run())


# ---------------------------------------------------------------------------
# engine-level connect / disconnect — bodies of Terrarium.connect /
# Terrarium.disconnect.  Kept here to share the channel-injection
# bookkeeping with :func:`inject_channel_trigger` etc.
# ---------------------------------------------------------------------------


async def connect_creatures(
    engine: "Terrarium",
    sender: "CreatureRef",
    receiver: "CreatureRef",
    *,
    channel: str | None = None,
) -> ConnectionResult:
    """Body of :meth:`Terrarium.connect` — see the engine docstring.

    Cross-graph connects merge the two graphs and union their
    environments so all creatures in the new combined graph see the
    same channel registry.
    """
    sid = engine._resolve_creature_id(sender)
    rid = engine._resolve_creature_id(receiver)
    sender_creature = engine.get_creature(sid)
    receiver_creature = engine.get_creature(rid)

    channel_name, delta = _topo.connect(engine._topology, sid, rid, channel=channel)
    if delta.kind == "merge":
        # The kept graph's id is delta.new_graph_ids[0].  Move every
        # channel + re-point every trigger from the dropped env into
        # the kept env, then drop the env entry for the orphan graph.
        keep_gid = delta.new_graph_ids[0]
        drop_gids = [g for g in delta.old_graph_ids if g != keep_gid]
        for drop_gid in drop_gids:
            _merge_environment_into(engine, keep_gid, drop_gid)
        # Update graph_id on every moved creature.
        for cid in delta.affected_creatures:
            c = engine._creatures.get(cid)
            if c is not None:
                c.graph_id = engine._topology.creature_to_graph.get(cid, c.graph_id)
        # Coordinate session-store side of the merge.
        _session_coord.apply_merge(engine, delta)
        # Mirror the kind promotion done by ``ensure_same_graph`` so
        # the rail's listing-by-kind doesn't flicker after a
        # channel-based connect either.
        _promote_session_kind_after_merge(keep_gid)
        _emit_session_kind_changed(engine, keep_gid, drop_gids, delta)

    gid = sender_creature.graph_id  # refreshed by the loop above
    env = engine._environments[gid]
    info = engine._topology.graphs[gid].channels[channel_name]
    register_channel_in_environment(
        env.shared_channels, info, engine=engine, graph_id=gid
    )
    # MERGE case: ``_merge_environment_into`` already injected the
    # listen trigger for *every* listen edge in the kept graph,
    # including the new one ``_topo.connect`` just added. Re-injecting
    # here causes a teardown / re-create race — the first inject's
    # outer ``_run`` task hasn't been scheduled yet, so
    # ``_teardown_existing_trigger`` finds nothing in ``_tasks`` and
    # silently leaves the orphan trigger task to subscribe + then get
    # torn down by a stray ``stop()`` after the second inject's task
    # has already bailed (because the first task is still ``done() ==
    # False``). Net effect: the channel ends up with zero subscribers
    # and the worker never receives sends. Skip the redundant inject.
    if delta.kind == "merge":
        trigger_id = f"channel_{receiver_creature.name}_{channel_name}"
    else:
        trigger_id = inject_channel_trigger(
            receiver_creature.agent,
            subscriber_id=receiver_creature.name,
            channel_name=channel_name,
            registry=env.shared_channels,
            ignore_sender=receiver_creature.name,
            ignore_sender_id=receiver_creature.creature_id,
        )
    if channel_name not in receiver_creature.listen_channels:
        receiver_creature.listen_channels.append(channel_name)
    if channel_name not in sender_creature.send_channels:
        sender_creature.send_channels.append(channel_name)

    # Always emit so engine subscribers (notably the runtime-graph
    # prompt block) refresh both creatures' system prompts. ``delta.kind``
    # is "merge" for cross-graph connects, "nothing" for intra-graph —
    # both still mutate listen/send lists, so prompts must update.
    engine._emit(
        EngineEvent(
            kind=EventKind.TOPOLOGY_CHANGED,
            graph_id=gid,
            payload={
                "kind": delta.kind,
                "old_graph_ids": list(delta.old_graph_ids),
                "new_graph_ids": list(delta.new_graph_ids),
                "affected": sorted(delta.affected_creatures)
                or sorted({sender_creature.creature_id, receiver_creature.creature_id}),
            },
        )
    )
    return ConnectionResult(
        channel=channel_name,
        trigger_id=trigger_id,
        delta_kind=delta.kind,
        graph_id=gid,
    )


async def ensure_same_graph(
    engine: "Terrarium", a: "CreatureRef", b: "CreatureRef"
) -> str:
    """Merge ``a``'s and ``b``'s graphs without creating a channel.

    Used by callers (e.g. cross-graph output wiring) that need both
    creatures in the same graph to function but don't want the
    channel/trigger side effects ``connect_creatures`` brings.  Returns
    the surviving graph id (unchanged if both creatures were already
    in the same graph).
    """
    sid = engine._resolve_creature_id(a)
    rid = engine._resolve_creature_id(b)
    a_gid = engine._topology.creature_to_graph[sid]
    b_gid = engine._topology.creature_to_graph[rid]
    if a_gid == b_gid:
        return a_gid
    delta = _topo._merge_graphs(engine._topology, a_gid, b_gid)
    keep_gid = delta.new_graph_ids[0]
    drop_gids = [g for g in delta.old_graph_ids if g != keep_gid]
    for drop_gid in drop_gids:
        _merge_environment_into(engine, keep_gid, drop_gid)
    for cid in delta.affected_creatures:
        creature = engine._creatures.get(cid)
        if creature is not None:
            creature.graph_id = engine._topology.creature_to_graph.get(
                cid, creature.graph_id
            )
    _session_coord.apply_merge(engine, delta)
    # Promote the surviving session's meta kind from "creature" to
    # "terrarium" when the merge produced a multi-creature graph so
    # the v2 rail (which splits the listing by kind) stops bouncing
    # between agentAPI.list and terrariumAPI.list as snapshots roll in.
    _promote_session_kind_after_merge(keep_gid)
    _emit_session_kind_changed(engine, keep_gid, drop_gids, delta)
    engine._emit(
        EngineEvent(
            kind=EventKind.TOPOLOGY_CHANGED,
            graph_id=keep_gid,
            payload={
                "kind": delta.kind,
                "old_graph_ids": list(delta.old_graph_ids),
                "new_graph_ids": list(delta.new_graph_ids),
                "affected": sorted(delta.affected_creatures),
            },
        )
    )
    return keep_gid


# Callback list invoked after every cross-graph merge. Higher tiers
# (notably ``studio.sessions.lifecycle``) register listeners here so
# they can react to merges (e.g. promote the surviving session's kind
# from "creature" to "terrarium" once it gains a second creature)
# without making this module import any studio/ symbol.  We use a
# registration hook rather than a lazy import to preserve the layer
# rule that ``terrarium/`` may not depend on ``studio/``.
_merge_listeners: list = []


def register_merge_listener(callback) -> None:
    """Register a ``callback(session_id: str)`` to fire after each
    successful graph merge.  Idempotent on identity."""
    if callback not in _merge_listeners:
        _merge_listeners.append(callback)


def _emit_session_kind_changed(
    engine: "Terrarium",
    keep_gid: str,
    drop_gids: list[str],
    delta: Any,
) -> None:
    """Emit a ``SESSION_KIND_CHANGED`` event after a graph merge.

    The frontend rail keys agent-vs-terrarium WS attach off session
    ``kind`` — without an explicit signal, a panel attached to a
    pre-merge solo creature keeps using the creature WS endpoint while
    the same id is now a terrarium session, and the user sees the
    mismatch as "messages disappear".  Emitting lets the frontend
    re-attach against the correct shape.
    """
    g = engine._topology.graphs.get(keep_gid)
    creature_count = len(g.creature_ids) if g is not None else 0
    new_kind = "terrarium" if creature_count > 1 else "creature"
    engine._emit(
        EngineEvent(
            kind=EventKind.SESSION_KIND_CHANGED,
            graph_id=keep_gid,
            payload={
                "session_id": keep_gid,
                "kind": new_kind,
                "absorbed_session_ids": list(drop_gids),
                "creature_count": creature_count,
                "delta_kind": getattr(delta, "kind", "merge"),
            },
        )
    )


def _promote_session_kind_after_merge(session_id: str) -> None:
    """Notify every registered listener that ``session_id`` has just
    absorbed another graph.  Listener exceptions are swallowed so a
    single misbehaving subscriber can't break the merge path."""
    for callback in list(_merge_listeners):
        try:
            callback(session_id)
        except Exception:
            logger.debug(
                "merge listener raised", listener=getattr(callback, "__name__", "?")
            )


def _merge_environment_into(engine: "Terrarium", keep_gid: str, drop_gid: str) -> None:
    """Union the dropped graph's environment into the surviving one.

    The topology merge has already happened by the time this is called,
    so ``keep_g.channels`` carries both graphs' channels. We iterate
    every post-merge channel and ensure it's registered in the kept
    env's :class:`ChannelRegistry` (idempotent for channels that were
    already there). Then we repoint every creature now in the kept
    graph at the kept env and re-inject their listen-channel triggers
    using the kept registry, so messages flowing into the kept env are
    actually delivered.
    """
    keep_env = engine._environments[keep_gid]
    drop_env = engine._environments.pop(drop_gid, None)
    if drop_env is None:
        return
    keep_g = engine._topology.graphs[keep_gid]

    # Copy channels.  Both registries store ``BaseChannel`` objects;
    # we re-create rather than alias so ownership is unambiguous.
    # Pass ``engine`` + ``graph_id`` so the persistence callback's
    # captured ``_terrarium_graph_id`` refreshes to the surviving gid.
    for ch_name, info in keep_g.channels.items():
        register_channel_in_environment(
            keep_env.shared_channels, info, engine=engine, graph_id=keep_gid
        )

    # Make sure the kept env carries a live engine handle so group tools
    # invoked on creatures that just got pulled into this graph can
    # resolve the engine.
    register_engine_handle(keep_env, engine)

    # Re-inject triggers for creatures whose graph_id is now keep_gid
    # but whose existing triggers still point at drop_env.
    for cid in keep_g.creature_ids:
        creature = engine._creatures.get(cid)
        if creature is None:
            continue
        # Repoint the agent's environment + executor at the surviving
        # one. Without this, tools that read ``context.environment``
        # (notably send_message resolving shared channels) keep seeing
        # the *dropped* registry — which is empty for a freshly-merged
        # solo creature, so every channel send fails with "Available
        # channels — none" even though the surviving graph has them.
        bind_creature_to_environment(creature, keep_env)
        for ch_name in keep_g.listen_edges.get(cid, set()):
            # remove any stale trigger (pointing at drop_env) and
            # inject a fresh one pointing at keep_env.
            remove_channel_trigger(
                creature.agent,
                subscriber_id=creature.name,
                channel_name=ch_name,
            )
            inject_channel_trigger(
                creature.agent,
                subscriber_id=creature.name,
                channel_name=ch_name,
                registry=keep_env.shared_channels,
                ignore_sender=creature.name,
                ignore_sender_id=creature.creature_id,
            )


# ``disconnect_creatures``, ``apply_split_bookkeeping``, and
# ``remove_channel_from_graph`` all live in
# :mod:`terrarium.channel_lifecycle`. Keeping them in the sibling
# module satisfies two constraints at once: the 600-line per-file
# budget on this file, and the layer-graph rule that
# ``channels`` <-> ``channel_lifecycle`` must not cycle.
