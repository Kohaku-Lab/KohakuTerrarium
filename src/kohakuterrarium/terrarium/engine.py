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

import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import kohakuterrarium.terrarium.channels as _channels
import kohakuterrarium.terrarium.recipe as _recipe
import kohakuterrarium.terrarium.resume as _resume
import kohakuterrarium.terrarium.root as _root
import kohakuterrarium.terrarium.topology as _topo
import kohakuterrarium.terrarium.wiring as _wiring
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.modules.output.base import OutputModule
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
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    ChannelKind,
    GraphTopology,
    TopologyState,
)
from kohakuterrarium.utils.logging import get_logger

if TYPE_CHECKING:
    from kohakuterrarium.session.store import SessionStore
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

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        *,
        pwd: str | None = None,
        session_dir: str | None = None,
    ) -> None:
        self._pwd = pwd
        # Where merge/split should land new ``.kohakutr`` files.  None
        # means "no persistence" — merged/split graphs reuse one of the
        # input stores in place rather than copy.
        self._session_dir = session_dir
        self._topology = TopologyState()
        self._creatures: dict[str, Creature] = {}
        self._environments: dict[str, Environment] = {}
        # graph_id -> attached SessionStore.
        self._session_stores: dict[str, "SessionStore"] = {}
        self._subscribers: list[_Subscriber] = []
        # Engine is alive as soon as constructed; ``shutdown()`` sets
        # this to False.  ``__aenter__`` is a no-op for this flag — it
        # only exists so users can guard cleanup with ``async with``.
        self._running = True

    @classmethod
    async def from_recipe(
        cls,
        recipe: "TerrariumConfig | str",
        *,
        pwd: str | None = None,
    ) -> "Terrarium":
        """Build a Terrarium from a recipe.  See :meth:`apply_recipe`.

        Example: ``async with await Terrarium.from_recipe("t.yaml") as t``.
        """
        engine = cls(pwd=pwd)
        await engine.apply_recipe(recipe, pwd=pwd)
        return engine

    @classmethod
    async def resume(
        cls,
        store: "SessionStore | str",
        *,
        pwd: str | None = None,
        llm_override: str | None = None,
    ) -> "Terrarium":
        """Build a fresh engine and adopt a saved session into it.

        Example: ``async with await Terrarium.resume("s.kohakutr") as t``.
        """
        engine = cls(pwd=pwd)
        engine._running = True
        await _resume.resume_into_engine(
            engine, store, pwd=pwd, llm_override=llm_override
        )
        return engine

    async def adopt_session(
        self,
        store: "SessionStore | str",
        *,
        pwd: str | None = None,
        llm_override: str | None = None,
    ) -> str:
        """Adopt a saved session into this running engine.  Returns ``graph_id``.

        Same body as :meth:`resume` but on an existing engine instance —
        the HTTP / programmatic hot-resume entry point.
        """
        return await _resume.resume_into_engine(
            self, store, pwd=pwd, llm_override=llm_override
        )

    @classmethod
    async def with_creature(
        cls,
        config: "CreatureBuildInput | Creature",
        *,
        pwd: str | None = None,
    ) -> "tuple[Terrarium, Creature]":
        """Construct a Terrarium and add a single creature in one call.

        Returns ``(terrarium, creature)``.  One-liner for solo agents::

            t, alice = await Terrarium.with_creature("alice.yaml")
        """
        engine = cls(pwd=pwd)
        creature = await engine.add_creature(config)
        return engine, creature

    # ------------------------------------------------------------------
    # async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "Terrarium":
        self._running = True
        return self

    async def __aexit__(self, *exc) -> None:
        await self.shutdown()

    # ------------------------------------------------------------------
    # creature CRUD
    # ------------------------------------------------------------------

    async def add_creature(
        self,
        config: "CreatureBuildInput | Creature",
        *,
        graph: GraphRef | None = None,
        creature_id: str | None = None,
        llm_override: str | None = None,
        pwd: str | None = None,
        start: bool = True,
    ) -> Creature:
        """Add a creature to the engine.

        ``config`` may be a path, ``AgentConfig``, ``CreatureConfig``,
        or a pre-built ``Creature`` (tests / advanced callers).  With
        ``graph=None`` a fresh singleton graph is minted.  ``start``
        toggles auto-start of the underlying agent.

        Example: ``alice = await t.add_creature("alice.yaml")``.
        """
        if isinstance(config, Creature):
            creature = config
        else:
            creature = build_creature(
                config,
                creature_id=creature_id,
                pwd=pwd if pwd is not None else self._pwd,
                llm_override=llm_override,
            )
        if creature_id and creature.creature_id != creature_id:
            creature.creature_id = creature_id
        if creature.creature_id in self._creatures:
            raise ValueError(f"creature_id {creature.creature_id!r} already exists")

        graph_id = self._resolve_graph_id(graph) if graph is not None else None
        gid = _topo.add_creature(
            self._topology, creature.creature_id, graph_id=graph_id
        )
        creature.graph_id = gid
        # Allocate or reuse the graph's environment.
        if gid not in self._environments:
            self._environments[gid] = Environment(env_id=f"env_{gid}")
        self._creatures[creature.creature_id] = creature

        if start:
            await creature.start()
        self._emit(
            EngineEvent(
                kind=EventKind.CREATURE_STARTED,
                creature_id=creature.creature_id,
                graph_id=gid,
            )
        )
        return creature

    async def remove_creature(self, creature: CreatureRef) -> None:
        """Stop and remove a creature.  May split the graph it lived in.

        Idempotent — removing an unknown creature raises ``KeyError``.
        """
        cid = self._resolve_creature_id(creature)
        c = self._creatures.get(cid)
        if c is None:
            raise KeyError(f"creature {cid!r} not in engine")
        old_gid = c.graph_id
        if c.is_running:
            await c.stop()
        delta = _topo.remove_creature(self._topology, cid)
        self._creatures.pop(cid, None)
        # Drop the environment if its graph went away.
        if old_gid not in self._topology.graphs:
            self._environments.pop(old_gid, None)
        self._emit(
            EngineEvent(
                kind=EventKind.CREATURE_STOPPED,
                creature_id=cid,
                graph_id=old_gid,
            )
        )
        if delta.kind != "nothing":
            self._emit(
                EngineEvent(
                    kind=EventKind.TOPOLOGY_CHANGED,
                    payload={
                        "kind": delta.kind,
                        "old_graph_ids": list(delta.old_graph_ids),
                        "new_graph_ids": list(delta.new_graph_ids),
                        "affected": sorted(delta.affected_creatures),
                    },
                )
            )

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
        kind: ChannelKind = ChannelKind.BROADCAST,
        description: str = "",
    ) -> ChannelInfo:
        """Declare a channel inside a graph.

        Channel names are graph-unique.  After declaration the channel
        exists in the graph's :class:`Environment.shared_channels`
        registry but no creature listens to or sends on it yet — use
        :meth:`connect` (or set listen/send via topology helpers) to
        wire creatures up.
        """
        gid = self._resolve_graph_id(graph)
        info = _topo.add_channel(
            self._topology,
            gid,
            name,
            kind=kind,
            description=description,
        )
        env = self._environments[gid]
        _channels.register_channel_in_environment(env.shared_channels, info)
        return info

    async def connect(
        self,
        sender: CreatureRef,
        receiver: CreatureRef,
        *,
        channel: str | None = None,
        kind: ChannelKind = ChannelKind.QUEUE,
    ) -> "ConnectionResult":
        """Wire a sender → receiver link via a channel.

        When the two creatures live in different graphs, the graphs
        merge — environments union, channels are pooled, and any
        attached session stores are merged into a single store on the
        surviving graph.

        Body lives in ``terrarium.channels.connect_creatures``.
        """
        return await _channels.connect_creatures(
            self, sender, receiver, channel=channel, kind=kind
        )

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
        ``terrarium.channels.disconnect_creatures``.
        """
        return await _channels.disconnect_creatures(
            self, sender, receiver, channel=channel
        )

    # ------------------------------------------------------------------
    # output wiring
    # ------------------------------------------------------------------

    async def wire_output(self, creature: CreatureRef, sink: OutputModule) -> str:
        """Attach a secondary output sink to a creature.

        Returns a sink id usable with :meth:`unwire_output`.
        """
        c = self._creature(creature)
        return _wiring.add_secondary_sink(c.agent, sink)

    async def unwire_output(self, creature: CreatureRef, sink_id: str) -> bool:
        """Remove a previously-attached sink.  Returns True if found."""
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
        """Designate ``creature`` as the root of its graph.

        Channel + wiring helper that mirrors the legacy "root agent"
        pattern: the root listens to every peer channel, every peer
        gains a send edge on a dedicated ``report_channel``.  Sets
        ``creature.is_root = True`` for downstream callers (UI mount,
        tool force-registration).  Body lives in
        :func:`terrarium.root.assign_root_to`.
        """
        return await _root.assign_root_to(self, creature, report_channel=report_channel)

    # ------------------------------------------------------------------
    # graphs
    # ------------------------------------------------------------------

    def get_graph(self, graph_id: str) -> GraphTopology:
        """Return the :class:`GraphTopology` for ``graph_id``."""
        g = self._topology.graphs.get(graph_id)
        if g is None:
            raise KeyError(f"graph {graph_id!r} does not exist")
        return g

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
        llm_override: str | None = None,
        creature_builder=None,
    ) -> GraphTopology:
        """Apply a terrarium recipe — bulk add_creature, add_channel,
        wire listen/send edges in dependency order.

        Body lives in :func:`terrarium.recipe.apply_recipe`.
        """
        return await _recipe.apply_recipe(
            self,
            recipe,
            graph=graph,
            pwd=pwd if pwd is not None else self._pwd,
            llm_override=llm_override,
            creature_builder=creature_builder,
        )

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def start(self, creature: CreatureRef) -> None:
        """Start a (previously-added) creature whose lifecycle was
        deferred via ``add_creature(..., start=False)``."""
        c = self._creature(creature)
        await c.start()

    async def stop(self, creature: CreatureRef) -> None:
        """Stop a running creature without removing it from the graph."""
        c = self._creature(creature)
        if c.is_running:
            await c.stop()

    async def stop_graph(self, graph: GraphRef) -> None:
        """Stop every creature in a graph (without removing them)."""
        gid = self._resolve_graph_id(graph)
        g = self._topology.graphs.get(gid)
        if g is None:
            return
        for cid in list(g.creature_ids):
            c = self._creatures.get(cid)
            if c is not None and c.is_running:
                await c.stop()

    async def shutdown(self) -> None:
        """Stop every creature in every graph.  Safe to call repeatedly.

        Called automatically by ``__aexit__``.
        """
        if not self._creatures and not self._running:
            return
        for c in list(self._creatures.values()):
            if c.is_running:
                try:
                    await c.stop()
                except Exception as e:  # pragma: no cover - defensive
                    _shutdown_log_warning(c.creature_id, str(e))
        self._running = False

    # ------------------------------------------------------------------
    # observability
    # ------------------------------------------------------------------

    async def subscribe(
        self, filter: EventFilter | None = None
    ) -> AsyncIterator[EngineEvent]:
        """Async-iterate engine events matching ``filter``.

        Each call returns a fresh async iterator with its own queue —
        events emitted before the iterator is awaited are not buffered.
        Cancelling / breaking out of the iterator de-registers the
        subscriber automatically.

        Example::

            async with Terrarium() as t:
                async for ev in t.subscribe():
                    print(ev.kind, ev.creature_id)
        """
        sub = _Subscriber(filter=filter)
        self._subscribers.append(sub)
        try:
            while True:
                ev = await sub.queue.get()
                if ev is None:
                    return
                yield ev
        finally:
            try:
                self._subscribers.remove(sub)
            except ValueError:
                pass

    def status(self, creature: CreatureRef | None = None) -> dict:
        """Status dict for one creature, or a roll-up if ``None``.

        The single-creature shape mirrors today's
        ``AgentSession.get_status()`` so existing API consumers don't
        notice the swap.  The roll-up shape (no argument) lists every
        creature plus graph membership.
        """
        if creature is not None:
            return self._creature(creature).get_status()
        return {
            "running": self._running,
            "creatures": {cid: c.get_status() for cid, c in self._creatures.items()},
            "graphs": {
                gid: {
                    "creature_ids": sorted(g.creature_ids),
                    "channels": sorted(g.channels),
                }
                for gid, g in self._topology.graphs.items()
            },
        }

    # ------------------------------------------------------------------
    # session attach
    # ------------------------------------------------------------------

    async def attach_session(self, graph: GraphRef, store: "SessionStore") -> None:
        """Attach a :class:`SessionStore` to a graph.  See
        ``terrarium.session_coord`` for merge/split details."""
        gid = self._resolve_graph_id(graph)
        self._session_stores[gid] = store
        g = self._topology.graphs.get(gid)
        if g is None:
            return
        for cid in g.creature_ids:
            c = self._creatures.get(cid)
            if c is None:
                continue
            if hasattr(c.agent, "attach_session_store"):
                c.agent.attach_session_store(store)
            elif hasattr(c.agent, "session_store"):
                c.agent.session_store = store

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

    def _emit(self, event: EngineEvent) -> None:
        """Fan out an event to every subscriber whose filter matches."""
        for sub in list(self._subscribers):
            if sub.filter is None or sub.filter.matches(event):
                try:
                    sub.queue.put_nowait(event)
                except Exception:  # pragma: no cover - defensive
                    pass


@dataclass
class _Subscriber:
    """Pub-sub bookkeeping for :meth:`Terrarium.subscribe`."""

    filter: EventFilter | None = None
    queue: "asyncio.Queue[EngineEvent | None]" = field(default_factory=asyncio.Queue)


def _shutdown_log_warning(creature_id: str, error: str) -> None:
    _logger.warning(
        "creature stop failed during shutdown",
        creature_id=creature_id,
        error=error,
    )
