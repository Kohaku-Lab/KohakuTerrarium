"""Terrarium runtime engine.

The engine hosts every running creature in the process and owns the
graph-level state (which creatures share a session, which channels
exist, who listens / sends).  A standalone ``kt run creature.yaml``
becomes a 1-creature graph; a multi-agent recipe becomes one or more
larger graphs.  Topology can change at runtime — channels can be
added or rewired between any pair of creatures, and the engine fans
the change out to live agents (channel-trigger injection, environment
union on graph merge, session-store copy on graph split).
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import kohakuterrarium.terrarium.autosession as _autosession
import kohakuterrarium.terrarium.channel_lifecycle as _lifecycle
import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.engine_creature_add as _engine_creature_add
import kohakuterrarium.terrarium.engine_observability as _observability
import kohakuterrarium.terrarium.graph_checkpoint as _checkpoint
import kohakuterrarium.terrarium.recipe as _recipe
import kohakuterrarium.terrarium.recipe_identity as _recipe_identity
import kohakuterrarium.terrarium.recipe_transaction as _recipe_transaction
import kohakuterrarium.terrarium.resume as _resume
import kohakuterrarium.terrarium.root as _root
import kohakuterrarium.terrarium.topology as _topo
import kohakuterrarium.terrarium.wiring as _wiring
import kohakuterrarium.terrarium.drive.runtime as _drive_runtime
from kohakuterrarium.terrarium.drive.store import (
    DriveRepositoryClosedError,
)
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.terrarium.creature_host import (
    Creature,
    CreatureBuildInput,
    build_creature,
)
from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
    EngineEvent,
    EventFilter,
    EventKind,
    RootAssignment,
)
import kohakuterrarium.terrarium.graph_identity_engine as _identity
from kohakuterrarium.terrarium.runtime_prompt import RuntimeGraphPrompt
from kohakuterrarium.terrarium.tools_group import (
    force_register_basic_tools,
    force_register_privileged_tools,
)
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    GraphTopology,
    TopologyDelta,
    TopologyState,
)
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.terrarium.config import TerrariumConfig

_logger = get_logger(__name__)

# A few user-facing aliases so callers can refer to creatures and graphs
# either by handle or by id.  The engine accepts both forms.
CreatureRef = Creature | str
GraphRef = GraphTopology | str


class Terrarium:
    """Multi-agent runtime engine.

    Hosts any number of creatures (single agents) and connects them via
    channels.  A standalone agent is a 1-creature graph; a "terrarium
    config" is a multi-creature graph.  Topology can change at runtime.
    See :meth:`from_recipe`, :meth:`resume`, :meth:`with_creature` for
    the three common construction shapes.
    """

    # Construction

    def __init__(
        self,
        *,
        pwd: str | None = None,
        session_dir: str | None = None,
        drive_config: Any = None,
        drive_registrations: "tuple[Any, ...] | list[Any] | None" = None,
        drive_store: Any = None,
    ) -> None:
        """Create an engine.

        ``session_dir`` turns on **autosession**: every new graph gets a
        ``<session_dir>/<graph_id>.kohakutr`` store automatically (and
        merge/split children land there too).  Without it, persistence
        is opt-in per call via ``add_creature(session=...)`` /
        ``apply_recipe(session=...)`` / ``attach_session``.

        Drive is enabled by default with fresh generic and goal registrations.
        ``DriveRuntimeConfig(enabled=False)`` explicitly opts out, while an
        explicitly empty registration set is invalid for an enabled runtime.
        ``drive_store`` overrides the default in-memory repository. The engine
        never reads Studio settings or ``~/.kohakuterrarium``.
        """
        self._pwd = pwd
        self._session_dir = session_dir
        # Built by default; an explicitly disabled config opts out.
        self._drive_runtime = _drive_runtime.build_drive_runtime(
            self, drive_config, drive_registrations, drive_store
        )
        self._topology = TopologyState()
        self._creatures: dict[str, Creature] = {}
        self._recipe_identities = _recipe_identity.RecipeIdentityReservations()
        self._recipe_graph_locks: dict[str, asyncio.Lock] = {}
        self._environments: dict[str, Environment] = {}
        self._session_stores: dict[str, "SessionStore"] = {}
        # graph_ids whose stores THIS engine minted (closed on shutdown).
        self._owned_sessions: set[str] = set()
        self._subscribers: list[_observability.Subscriber] = []
        self._running = True
        # Live runtime-graph prompt block — refreshed reactively when the
        # engine emits topology / wire / parent-link events. Attaches
        # lazily on the first ``async with`` / ``__aenter__`` so a sync
        # construction in tests doesn't require an event loop.
        self._runtime_prompt = RuntimeGraphPrompt(self)

    @classmethod
    async def from_recipe(
        cls,
        recipe: "TerrariumConfig | str",
        *,
        pwd: str | None = None,
        drive_config: Any = None,
        drive_registrations: "tuple[Any, ...] | list[Any] | None" = None,
        drive_store: Any = None,
    ) -> "Terrarium":
        """Build a Terrarium from a recipe.

        Drive configuration belongs to the engine constructor and is never read
        from the recipe. See :meth:`apply_recipe`.
        """
        engine = cls(
            pwd=pwd,
            drive_config=drive_config,
            drive_registrations=drive_registrations,
            drive_store=drive_store,
        )
        await engine.apply_recipe(recipe, pwd=pwd)
        return engine

    @classmethod
    async def resume(
        cls,
        store: "SessionStore | str",
        *,
        pwd: str | None = None,
        workspace_overrides: dict[str, str] | None = None,
        llm: Any = None,
        drive_config: Any = None,
        drive_registrations: "tuple[Any, ...] | list[Any] | None" = None,
        drive_store: Any = None,
    ) -> "Terrarium":
        """Preflight and adopt a saved session into a fresh engine.

        A failed preflight cannot create Drive/runtime side effects."""
        return await _resume.resume_new_engine(
            cls,
            store,
            pwd=pwd,
            workspace_overrides=workspace_overrides,
            llm=llm,
            drive_config=drive_config,
            drive_registrations=drive_registrations,
            drive_store=drive_store,
        )

    async def adopt_session(
        self,
        store: "SessionStore | str",
        *,
        pwd: str | None = None,
        workspace_overrides: dict[str, str] | None = None,
        llm: Any = None,
    ) -> str:
        """Adopt a saved session into this engine and return its graph ID."""
        kwargs = {"pwd": pwd, "llm": llm}
        if workspace_overrides is not None:
            kwargs["workspace_overrides"] = workspace_overrides
        return await _resume.resume_into_engine(self, store, **kwargs)

    @classmethod
    async def with_creature(
        cls,
        config: "CreatureBuildInput | Creature",
        *,
        pwd: str | None = None,
        drive_config: Any = None,
        drive_registrations: "tuple[Any, ...] | list[Any] | None" = None,
        drive_store: Any = None,
    ) -> "tuple[Terrarium, Creature]":
        """Construct a Terrarium and add a single creature in one call; Drive
        args go to the constructor. Returns ``(terrarium, creature)``::

            t, alice = await Terrarium.with_creature("alice.yaml")
        """
        engine = cls(
            pwd=pwd,
            drive_config=drive_config,
            drive_registrations=drive_registrations,
            drive_store=drive_store,
        )
        creature = await engine.add_creature(config)
        return engine, creature

    # ------------------------------------------------------------------
    # async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "Terrarium":
        self._running = True
        self._runtime_prompt.attach()
        if self._drive_runtime is not None:
            await self._drive_runtime.start()
        return self

    async def __aexit__(self, *exc) -> None:
        self._runtime_prompt.detach()
        await self.shutdown()

    # ------------------------------------------------------------------
    # Drive runtime (optional; None when the engine is Drive-disabled)
    # ------------------------------------------------------------------

    @property
    def drives(self):
        """The engine's :class:`DriveRuntime`, or ``None`` when Drive-disabled.

        The façade over the DriveManager (create / list / update / assign /
        transition Drives, reconcile, reconfigure).  ``None`` means the engine
        was constructed without ``drive_config`` — no manager exists.
        """
        return self._drive_runtime

    def reconfigure_drives(self, drive_registrations) -> str:
        """Apply a Drive registry change; returns ``applied_live`` /
        ``restart_required`` / ``rejected``. Raises when the
        engine has no Drive runtime."""
        if self._drive_runtime is None:
            raise RuntimeError("this terrarium has no Drive runtime to reconfigure")
        return self._drive_runtime.reconfigure(drive_registrations)

    # creature CRUD

    async def add_creature(
        self,
        config: CreatureBuildInput | Creature,
        *,
        graph: GraphRef | None = None,
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
        _identity_reserved: bool = False,
    ) -> Creature:
        """Build and insert one creature into the engine."""
        return await _engine_creature_add.add_creature(
            self,
            config,
            graph=graph,
            creature_id=creature_id,
            llm=llm,
            pwd=pwd,
            start=start,
            is_privileged=is_privileged,
            parent_creature_id=parent_creature_id,
            io=io,
            strict=strict,
            session=session,
            name=name,
            tools=tools,
            plugins=plugins,
            identity_reserved=_identity_reserved,
            builder=build_creature,
            register_basic=force_register_basic_tools,
            register_privileged=force_register_privileged_tools,
        )

    async def remove_creature(self, creature: CreatureRef) -> None:
        """Stop and remove a creature.  May split the graph it lived in.

        Raises ``KeyError`` when the creature is not in the engine.
        """
        cid = self._resolve_creature_id(creature)
        c = self._creatures.get(cid)
        if c is None:
            raise KeyError(f"creature {cid!r} not in engine")
        old_gid = c.graph_id
        if c.is_running:
            await c.stop(requested=False)
        # A creature-scoped Drive orphans-and-blocks on removal,
        # a graph-scoped one unassigns / auto-assigns among the remaining
        # graph members — never a silent semantic reassignment.
        # Drive cleanup must never block the creature removal itself: the
        # repository may already be closed (Windows closes a session-backed
        # repo's sqlite connection when its store closes; a stale registry
        # entry can survive a merge/split). Treat that as quiescence and
        # continue the topology removal below.
        if self._drive_runtime is not None:
            old_graph = self._topology.graphs.get(old_gid)
            members = (
                frozenset(old_graph.creature_ids) - {cid}
                if old_graph is not None
                else frozenset()
            )
            try:
                await self._drive_runtime.on_creature_removed(
                    cid, graph_id=old_gid, graph_member_ids=members
                )
            except DriveRepositoryClosedError as exc:
                _logger.warning(
                    "Drive cleanup skipped during creature removal "
                    "(repository closed)",
                    creature_id=cid,
                    error=str(exc),
                )
        delta = _topo.remove_creature(self._topology, cid)
        self._creatures.pop(cid, None)
        _wiring.install_output_wiring_resolver(self)
        # Drop the environment + the graph's Drive manager if the graph went
        # away entirely (a split re-homes the manager instead).
        if old_gid not in self._topology.graphs:
            self._environments.pop(old_gid, None)
            await _checkpoint.checkpoint(self, old_gid)
            _checkpoint.discard(self, old_gid)
            if self._drive_runtime is not None:
                self._drive_runtime.registry.drop_graph(old_gid)
        self._emit(
            EngineEvent(
                kind=EventKind.CREATURE_STOPPED,
                creature_id=cid,
                graph_id=old_gid,
            )
        )
        # Removing a creature can split the graph (when it was the only
        # bridge between two clusters). Run the shared bookkeeping so
        # new envs are allocated, surviving creatures are repointed at
        # their new graph_id, and session stores are coordinated.
        # ``apply_split_bookkeeping`` is a no-op for non-split deltas.
        _lifecycle.apply_split_bookkeeping(self, delta)
        await _checkpoint.checkpoint_many(self, delta.new_graph_ids)
        await self._drain_drive_topology()

    def get_creature(self, creature_id: str) -> Creature:
        """Return the creature with the given id.  Raises ``KeyError``."""
        c = self._creatures.get(creature_id)
        if c is None:
            raise KeyError(f"creature {creature_id!r} not in engine")
        return c

    def list_creatures(self) -> list[Creature]:
        """All currently-hosted creatures."""
        return list(self._creatures.values())

    # ------------------------------------------------------------------
    # pythonic accessors
    # ------------------------------------------------------------------

    def __getitem__(self, creature_id: str) -> Creature:
        return self.get_creature(creature_id)

    def __contains__(self, creature_id: str) -> bool:
        return creature_id in self._creatures

    def __iter__(self) -> Iterator[Creature]:
        return iter(self.list_creatures())

    def __len__(self) -> int:
        return len(self._creatures)

    # ------------------------------------------------------------------
    # channel CRUD
    # ------------------------------------------------------------------

    async def add_channel(
        self,
        graph: GraphRef,
        name: str,
        description: str = "",
    ) -> ChannelInfo:
        """Declare a channel inside a graph.

        Channel names are graph-unique. Graph topology channels are
        always broadcast — every listener receives every send. After
        declaration the channel exists in the graph's
        :class:`Environment.shared_channels` registry but no creature
        listens to or sends on it yet — use :meth:`connect` (or set
        listen/send via topology helpers) to wire creatures up.
        """
        gid = self._resolve_graph_id(graph)
        info = _topo.add_channel(
            self._topology,
            gid,
            name,
            description=description,
        )
        env = self._environments[gid]
        _channels.register_channel_in_environment(
            env.shared_channels, info, engine=self, graph_id=gid
        )
        await _checkpoint.checkpoint(self, gid)
        return info

    def environment(self, graph: GraphRef):
        """Public handle for a graph's live :class:`Environment`.

        Raises ``KeyError`` for an unknown graph.  The previous way to
        reach a channel programmatically was ``engine._environments``
        — a private dict that every example and e2e test poked anyway.
        """
        gid = self._resolve_graph_id(graph)
        return self._environments[gid]

    def channel(self, graph: GraphRef, name: str):
        """Live channel handle (send / history) or ``None``.

        The returned object is the broadcast channel itself — scripts
        can ``await ch.send(ChannelMessage(...))`` to seed a graph or
        read ``ch.history`` to observe traffic.
        """
        return self.environment(graph).shared_channels.get(name)

    async def remove_channel(self, graph: GraphRef, name: str) -> TopologyDelta:
        """Remove a channel from a graph.

        Tears down listen triggers, drops the channel from the live
        registry and topology, and may split the graph if the channel
        was the only connectivity bridge between two components. Body
        in ``terrarium.channels.remove_channel_from_graph``.
        """
        gid = self._resolve_graph_id(graph)
        delta = await _lifecycle.remove_channel_from_graph(self, gid, name)
        # remove_channel may auto-split; snapshot every store-attached
        # graph so each one reflects its post-removal topology.
        await _checkpoint.checkpoint_many(self, delta.new_graph_ids)
        await self._drain_drive_topology()
        return delta

    async def connect(
        self,
        sender: CreatureRef,
        receiver: CreatureRef,
        *,
        channel: str | None = None,
    ) -> "ConnectionResult":
        """Wire a sender → receiver link via a channel.

        When the two creatures live in different graphs, the graphs
        merge — environments union, channels are pooled, and any
        attached session stores are merged into a single store on the
        surviving graph.

        Body lives in ``terrarium.channels.connect_creatures``.
        """
        endpoints = _identity.resolve_and_guard_connect(self, sender, receiver)
        result = await _channels.connect_creatures(self, *endpoints, channel=channel)
        await _checkpoint.checkpoint(self, result.graph_id)
        await self._drain_drive_topology()
        return result

    async def disconnect(
        self,
        sender: CreatureRef,
        receiver: CreatureRef,
        *,
        channel: str | None = None,
    ) -> "DisconnectionResult":
        """Drop a sender → receiver link.  May split a graph.

        When ``channel`` is None, every sender→receiver edge is
        unwired.  Body lives in
        ``terrarium.channel_lifecycle.disconnect_creatures``.
        """
        result = await _lifecycle.disconnect_creatures(
            self, sender, receiver, channel=channel
        )
        graph_ids = (
            graph.graph_id
            for graph in self.list_graphs()
            if graph.graph_id in self._session_stores
        )
        await _checkpoint.checkpoint_many(self, graph_ids)
        await self._drain_drive_topology()
        return result

    # ------------------------------------------------------------------
    # output wiring
    # ------------------------------------------------------------------

    async def wire_output(self, creature: CreatureRef, target) -> str:
        """Add a runtime ``config.output_wiring`` edge; return its id."""
        c = self._creature(creature)
        edge_id = _wiring.add_output_edge(c.agent, target)
        self._emit(
            EngineEvent(
                kind=EventKind.OUTPUT_WIRE_ADDED,
                creature_id=c.creature_id,
                graph_id=c.graph_id,
                payload={"edge_id": edge_id},
            )
        )
        await _checkpoint.checkpoint(self, c.graph_id)
        return edge_id

    async def unwire_output(self, creature: CreatureRef, edge_id: str) -> bool:
        """Remove a runtime ``config.output_wiring`` edge by id."""
        c = self._creature(creature)
        removed = _wiring.remove_output_edge(c.agent, edge_id)
        if removed:
            self._emit(
                EngineEvent(
                    kind=EventKind.OUTPUT_WIRE_REMOVED,
                    creature_id=c.creature_id,
                    graph_id=c.graph_id,
                    payload={"edge_id": edge_id},
                )
            )
            await _checkpoint.checkpoint(self, c.graph_id)
        return removed

    def list_output_wiring(self, creature: CreatureRef) -> list[dict]:
        """List output-wiring edges on a creature."""
        c = self._creature(creature)
        return _wiring.list_output_edges(c.agent)

    async def wire_output_sink(self, creature: CreatureRef, sink) -> str:
        """Attach a secondary output sink to a creature."""
        c = self._creature(creature)
        return _wiring.add_secondary_sink(c.agent, sink)

    async def unwire_output_sink(self, creature: CreatureRef, sink_id: str) -> bool:
        """Remove a secondary output sink."""
        c = self._creature(creature)
        return _wiring.remove_secondary_sink(c.agent, sink_id)

    # ------------------------------------------------------------------
    # root assignment — graph-level helper
    # ------------------------------------------------------------------

    async def assign_root(
        self,
        creature: CreatureRef,
        *,
        report_channel: str = "report_to_root",
    ) -> RootAssignment:
        """Designate ``creature`` as the privileged root of its graph.

        Group-scoped helper — operates only on the creature's current
        graph. Side effects:

        - Declares ``report_channel`` if missing.
        - Wires the root as listener on every channel in the graph.
        - Wires every other creature as sender on ``report_channel``.
        - Sets ``creature.is_privileged = True`` (elevate-only — already
          privileged creatures stay privileged).
        - Force-registers the ``group_*`` tools on the root agent.

        Body lives in :func:`terrarium.root.assign_root_to`.
        """
        result = await _root.assign_root_to(
            self, creature, report_channel=report_channel
        )
        await _checkpoint.checkpoint(self, result.graph_id)
        return result

    # ------------------------------------------------------------------
    # graphs
    # ------------------------------------------------------------------

    def get_graph(self, graph_id: str) -> GraphTopology:
        """Return the :class:`GraphTopology` for ``graph_id``."""
        g = self._topology.graphs.get(graph_id)
        if g is None:
            raise KeyError(f"graph {graph_id!r} does not exist")
        return g

    def _create_restore_graph(self, graph_id: str) -> GraphTopology:
        """Create an empty graph with a session-persisted identifier."""
        graph = _topo.create_graph(self._topology, graph_id)
        self._environments[graph_id] = Environment(env_id=f"env_{graph_id}")
        return graph

    def list_graphs(self) -> list[GraphTopology]:
        """All currently-active graphs."""
        return list(self._topology.graphs.values())

    # ------------------------------------------------------------------
    # recipe
    # ------------------------------------------------------------------

    async def apply_recipe(
        self,
        recipe,
        *,
        graph: GraphRef | None = None,
        pwd: str | None = None,
        llm: Any = None,
        strict: bool = True,
        start: bool = True,
        session: "bool | str | Path | SessionStore | None" = None,
        creature_builder=None,
        created_ids: list[str] | None = None,
    ) -> GraphTopology:
        """Apply a terrarium recipe into this engine.

        ``session`` follows ``add_creature`` and creates one graph store.
        ``created_ids`` collects only creatures added by this call.
        """
        kwargs = {
            "graph": graph,
            "pwd": pwd if pwd is not None else self._pwd,
            "strict": strict,
            "start": start,
            "creature_builder": creature_builder,
            "created_ids": created_ids,
        }
        if llm is not None:
            kwargs["llm"] = llm
        transaction = _recipe_transaction.RecipeApplyTransaction(self)
        kwargs["transaction"] = transaction
        topo = None
        try:
            with _checkpoint.suppress(self):
                topo = await _recipe.apply_recipe(self, recipe, **kwargs)
                if topo is not None:
                    previous_store = self._session_stores.get(topo.graph_id)
                    if (
                        previous_store is not None
                        and session is not False
                        and not _autosession.recipe_session_reuses_store(
                            previous_store, session
                        )
                    ):
                        transaction.stage_session_replacement(topo.graph_id)
                    await _autosession.attach_for_recipe(
                        self, topo.graph_id, recipe=recipe, session=session
                    )
            if topo is not None:
                await _checkpoint.checkpoint(self, topo.graph_id)
        except BaseException:
            await _recipe_transaction.rollback_shielded(transaction)
            raise
        await transaction.commit()
        return topo

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self, creature: CreatureRef) -> None:
        """Start a (previously-added) creature whose lifecycle was
        deferred via ``add_creature(..., start=False)``."""
        c = self._creature(creature)
        await c.start()
        # Re-arm Drive reconciliation behind the restoration barrier.
        if self._drive_runtime is not None:
            self._drive_runtime.schedule_reconcile(c)

    async def stop(self, creature: CreatureRef) -> None:
        """Stop a running creature without removing it from the graph."""
        c = self._creature(creature)
        await c.stop()
        if self._drive_runtime is not None:
            await self._drive_runtime.on_creature_stopped(c.creature_id)

    async def stop_graph(self, graph: GraphRef) -> None:
        """Stop every creature in a graph (without removing them)."""
        gid = self._resolve_graph_id(graph)
        g = self._topology.graphs.get(gid)
        if g is None:
            return
        for cid in list(g.creature_ids):
            c = self._creatures.get(cid)
            if c is not None:
                await c.stop()
                if self._drive_runtime is not None:
                    await self._drive_runtime.on_creature_stopped(cid)

    async def shutdown(self) -> None:
        """Stop every creature in every graph.  Safe to call repeatedly.

        Called automatically by ``__aexit__``.
        """
        if not self._creatures and not self._running:
            return
        # The stop loop can be cancelled mid-await; run store closure +
        # subscriber teardown in ``finally`` so a leaked writer lock (which
        # blocks any later adopt of the same file) can't outlive shutdown.
        try:
            # Stop claiming new Drive deliveries + drain settlements BEFORE
            # creatures stop and owned stores close.
            if self._drive_runtime is not None:
                try:
                    await self._drive_runtime.stop()
                except Exception as e:  # pragma: no cover - defensive
                    _logger.warning("drive runtime stop failed", error=str(e))
            for c in list(self._creatures.values()):
                if c.is_running:
                    try:
                        await c.stop(requested=False)
                    except Exception as e:  # pragma: no cover - defensive
                        _shutdown_log_warning(c.creature_id, str(e))
        finally:
            for graph_id in sorted(self._session_stores):
                try:
                    await _checkpoint.checkpoint(self, graph_id)
                except Exception as e:
                    _logger.warning(
                        "final graph manifest checkpoint failed",
                        graph_id=graph_id,
                        error=str(e),
                    )
            # Close every store this engine minted — without this, files
            # stay status="running" forever (the HW4 case: 61 stuck files).
            _autosession.close_owned_stores(self)
            # Terminate live subscribers — ``async for ev in t.subscribe()``
            # used to hang forever after shutdown.
            for sub in list(self._subscribers):
                try:
                    sub.queue.put_nowait(None)
                except Exception:  # pragma: no cover - defensive
                    pass
            self._running = False

    # ------------------------------------------------------------------
    # observability
    # ------------------------------------------------------------------

    def subscribe(
        self, filter: EventFilter | None = None
    ) -> AsyncIterator[EngineEvent]:
        """Async-iterate engine events matching ``filter``.

        The subscriber registers IMMEDIATELY at this call (not on the
        first ``await``) — events emitted between ``subscribe()`` and
        the start of iteration are buffered, so the
        subscribe-then-trigger pattern can't lose its first event.
        Cancelling / breaking out of the iterator de-registers the
        subscriber automatically; ``shutdown()`` terminates it.

        Example::

            async with Terrarium() as t:
                async for ev in t.subscribe():
                    print(ev.kind, ev.creature_id)
        """
        sub = _observability.Subscriber(filter=filter)
        self._subscribers.append(sub)
        return _observability.subscription_iter(self, sub)

    def status(self, creature: CreatureRef | None = None) -> dict:
        """Status dict for one creature, or a roll-up if ``None``.

        The single-creature shape mirrors :meth:`Creature.get_status` —
        the same shape every API / WS endpoint reads. The roll-up
        shape (no argument) lists every creature plus graph membership.
        """
        return _observability.status(self, creature)

    # ------------------------------------------------------------------
    # session attach
    # ------------------------------------------------------------------

    async def attach_session(
        self, graph: GraphRef, store: "SessionStore | str | Path"
    ) -> None:
        """Attach a :class:`SessionStore` to a graph.

        ``store`` may also be a path — mint-mode: the engine creates the
        store there with validated meta (and closes it on shutdown).
        See ``terrarium.session_coord`` for merge/split details.
        """
        gid = self._resolve_graph_id(graph)
        # Replacing a graph's store: close the previous one first so its
        # native handles + writer lock are released before the new (or a
        # freshly-minted) store opens the same file. Detach the graph's Drive
        # manager first so its dispatcher releases claims against a LIVE
        # connection, not the companion repo the store close is about to drop.
        previous = self._session_stores.get(gid)
        if previous is not None and previous is not store:
            if self._drive_runtime is not None:
                await self._drive_runtime.detach_graph(gid)
            try:
                previous.close(update_status=False)
            except Exception:  # pragma: no cover - defensive
                _logger.warning(
                    "attach_session: closing replaced store failed", exc_info=True
                )
            self._owned_sessions.discard(gid)
        if isinstance(store, (str, Path)):
            names = [c.name for c in self._creatures.values() if c.graph_id == gid]
            store = _autosession.mint_store(self, gid, path=store, agents=names)
            self._owned_sessions.add(gid)
        self._session_stores[gid] = store
        # Bind the graph's session-backed Drive repository before its creatures
        # reach restoration-ready.
        if self._drive_runtime is not None:
            await self._drive_runtime.bind_graph_store(gid, store)
        g = self._topology.graphs.get(gid)
        if g is None:
            return
        # Retroactively wire channel persistence on every channel that
        # was registered before the store was attached — without this,
        # channels created at engine.add_channel time before
        # attach_session lose every send to the void.
        env = self._environments.get(gid)
        if env is not None:
            for channel in env.shared_channels._channels.values():
                _channels._ensure_channel_persistence(channel, self, gid)
        for cid in g.creature_ids:
            c = self._creatures.get(cid)
            if c is None:
                continue
            if hasattr(c.agent, "attach_session_store"):
                c.agent.attach_session_store(store)
            elif hasattr(c.agent, "session_store"):
                c.agent.session_store = store
        await _checkpoint.checkpoint(self, gid)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _resolve_creature_id(self, ref: CreatureRef) -> str:
        if isinstance(ref, Creature):
            return ref.creature_id
        return ref

    def _resolve_graph_id(self, ref: GraphRef) -> str:
        if isinstance(ref, GraphTopology):
            return ref.graph_id
        return ref

    def _creature(self, ref: CreatureRef) -> Creature:
        return self.get_creature(self._resolve_creature_id(ref))

    async def checkpoint_graph(self, graph: GraphRef) -> bool:
        """Persist the current authoritative manifest for one graph."""
        return await _checkpoint.checkpoint(self, self._resolve_graph_id(graph))

    async def _drain_drive_topology(self) -> None:
        """Apply any Drive row movement stashed by a merge or split."""
        if self._drive_runtime is not None:
            await self._drive_runtime.drain_topology()

    def _emit(self, event: EngineEvent) -> None:
        """Fan out an event to every subscriber whose filter matches."""
        _observability.emit(self, event)


def _shutdown_log_warning(creature_id: str, error: str) -> None:
    _logger.warning(
        "creature stop failed during shutdown",
        creature_id=creature_id,
        error=error,
    )
