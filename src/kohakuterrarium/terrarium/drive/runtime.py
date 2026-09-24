"""Engine-side glue binding per-graph :class:`DriveManager`\\ s to real creatures.

:class:`DriveRuntime` is owned by a Drive-enabled :class:`Terrarium`. A
:class:`~kohakuterrarium.terrarium.drive.partition.GraphDriveRegistry` holds one
repository and manager per graph, so a Drive created in graph A
is invisible to graph B and topology merge/split moves rows between graph
repositories (see :mod:`~kohakuterrarium.terrarium.drive.topology`).

The runtime still owns the engine sink that delivers over ``Creature.inject_event``,
maps :class:`DriveObservation`\\ s to :class:`EngineEvent`\\ s, gates per-creature
reconciliation on the restoration barrier (§6.5), binds session-backed
repositories before restoration-ready (§7.1), drains on shutdown (§6.4), and
applies live registry reconfigures (§8.6). The engine keeps only thin call sites.
"""

import asyncio
import random
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kohakuterrarium.terrarium.channels import register_drive_service
from kohakuterrarium.terrarium.creature_ids import (
    _clean_creature_name,
    _decode_creature_name,
)
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
    validate_runtime_selection,
)
from kohakuterrarium.terrarium.drive.requests import DriveQuery
from kohakuterrarium.terrarium.drive.injection import (
    install_drive_runtime,
    refresh_drive_prompt,
)
import kohakuterrarium.terrarium.drive.topology as _topology
from kohakuterrarium.terrarium.drive.manager import DriveManager
from kohakuterrarium.terrarium.drive.partition import GraphDriveRegistry
from kohakuterrarium.terrarium.drive.registration import DriveRegistration
from kohakuterrarium.terrarium.drive.sink import (
    DeliveryOutcome,
    DriveObservation,
    DriveObserver,
    Settlement,
    SettlementSource,
    SettlementStatus,
)
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot
from kohakuterrarium.terrarium.drive.split_intent import recover_split_intents
from kohakuterrarium.terrarium.drive.store import DriveRepositoryClosedError
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

APPLIED_LIVE = "applied_live"
RESTART_REQUIRED = "restart_required"
REJECTED = "rejected"

# Internal delivery signals stay off the public engine bus. Unknown signals are
# logged because they may represent a missing public EventKind declaration.
_INTERNAL_OBSERVATION_KINDS = frozenset(
    {
        "drive_backpressured",
        "drive_yielded",
        "drive_delivery_deferred",
        "drive_delivery_superseded",
        "drive_delivery_replayed",
        "drive_owner_transferred",
        "drive_reassigned",
        "drive_readiness_error",
    }
)


def build_drive_runtime(
    engine: Any,
    drive_config: DriveRuntimeConfig | None,
    drive_registrations: "list[DriveRegistration] | tuple[DriveRegistration, ...] | None",
    drive_store: Any,
) -> "DriveRuntime | None":
    """Build the engine's Drive runtime unless configuration disables it."""
    config = drive_config if drive_config is not None else DriveRuntimeConfig()
    registrations = (
        default_registrations() if drive_registrations is None else drive_registrations
    )
    validate_runtime_selection(config, registrations)
    if not config.enabled:
        return None
    return DriveRuntime(engine, config, tuple(registrations), store=drive_store)


def _result_to_settlement(result: Any) -> Settlement:
    """Map a turn result to the corresponding Drive settlement."""
    status = getattr(result, "status", "ok")
    detail: dict[str, Any] = {}
    correlation = getattr(result, "correlation_id", None)
    if correlation:
        detail["correlation_id"] = correlation
    if getattr(result, "interrupted_by_user", False):
        detail["interrupted_by_user"] = True
        return Settlement(SettlementStatus.INTERRUPTED, detail)
    if status == "ok":
        return Settlement(SettlementStatus.OK, detail)
    if status in ("interrupted", "rejected"):
        # Stops and interrupts are transient, so they must not consume the hard
        # failure budget.
        return Settlement(SettlementStatus.INTERRUPTED, detail)
    detail["error"] = getattr(result, "error", None) or status
    return Settlement(SettlementStatus.ERROR, detail)


class _EngineDriveSink:
    """Deliver Drive events through engine-owned creatures and track settlement."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def deliver(
        self, creature_id: str, event: Any, *, delivery_id: str
    ) -> DeliveryOutcome:
        creature = self._engine._creatures.get(creature_id)
        # Paused creatures remain running but cannot admit turns; treating them
        # as stopped defers delivery without consuming the retry budget.
        if (
            creature is None
            or creature.stop_requested
            or getattr(creature, "paused", False)
        ):
            return DeliveryOutcome.rejected_stopped()
        # Delivery must wait until restoration finishes so persisted state is
        # available before the creature handles the Drive.
        if not getattr(creature, "restoration_ready", True):
            return DeliveryOutcome.rejected_stopped()
        if not creature.is_running:
            if not creature.is_naturally_idle():
                return DeliveryOutcome.rejected_stopped()
            await creature.start(requested=False)
            if creature.stop_requested or not creature.is_running:
                return DeliveryOutcome.rejected_stopped()
            await creature.wait_restoration_ready()
            if creature.stop_requested or not creature.is_running:
                return DeliveryOutcome.rejected_stopped()
        if creature.stop_requested or not creature.is_running:
            return DeliveryOutcome.rejected_stopped()
        task = asyncio.ensure_future(
            creature.inject_event(event, correlation_id=delivery_id)
        )

        async def _settle() -> Settlement:
            result = await task
            return _result_to_settlement(result)

        settlement: SettlementSource = _settle
        return DeliveryOutcome.accepted(settlement)

    def has_queued_foreign_work(self, creature_id: str) -> bool:
        creature = self._engine._creatures.get(creature_id)
        if creature is None:
            return False
        return bool(creature.agent.has_pending_mid_turn_inputs)


class _GraphScopedDriveRegistry(GraphDriveRegistry):
    """Bind each graph manager's observations to its graph identity."""

    def __init__(
        self, *, observer_factory: Callable[[str], DriveObserver], **kwargs: Any
    ) -> None:
        super().__init__(observer=None, **kwargs)
        self._observer_factory = observer_factory

    def _build_manager(self, graph_id: str, repo: Any) -> DriveManager:
        # The base builder reads a shared observer slot, so restore it after the
        # graph-specific manager has captured its observer.
        prev = self._observer
        self._observer = self._observer_factory(graph_id)
        try:
            return super()._build_manager(graph_id, repo)
        finally:
            self._observer = prev


class DriveRuntime:
    """Coordinate graph-scoped Drive storage, delivery, and lifecycle."""

    def __init__(
        self,
        engine: Any,
        config: DriveRuntimeConfig,
        registrations: tuple[DriveRegistration, ...],
        *,
        store: Any = None,
        clock: Callable[[], datetime] | None = None,
        rng: Any = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        validate_runtime_selection(config, registrations)
        self._engine = engine
        self._config = config
        self._snapshot = EnabledRegistrySnapshot.build(registrations)
        self._sink = _EngineDriveSink(engine)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._started = False
        # Reconcile tasks are grouped by graph so repository shutdown can await
        # every task that may still access that graph.
        self._reconcile_tasks: dict[str, set[asyncio.Task]] = {}
        # A creature may have only one barrier-gated reconcile pending at once.
        self._reconcile_creatures: set[str] = set()
        self._registry = _GraphScopedDriveRegistry(
            engine=engine,
            config=config,
            get_snapshot=lambda: self._snapshot,
            sink=self._sink,
            observer_factory=self._observer_for_graph,
            clock=self._clock,
            rng=rng or random.Random(),
            id_factory=self._id_factory,
            store=store,
        )

    @property
    def manager(self) -> DriveManager:
        """Return the sole manager, rejecting zero- or multi-graph ambiguity."""
        managers = self._registry.all_managers()
        if len(managers) == 1:
            return managers[0]
        if not managers:
            raise RuntimeError("no Drive manager exists yet (no graph has one)")
        raise RuntimeError(
            "engine hosts multiple Drive graphs; use manager_for(graph_id)"
        )

    @property
    def registry(self) -> GraphDriveRegistry:
        return self._registry

    @property
    def snapshot(self) -> EnabledRegistrySnapshot:
        return self._snapshot

    @property
    def config(self) -> DriveRuntimeConfig:
        return self._config

    @property
    def durability(self) -> str:
        return self._registry.durability

    def durability_for(self, graph_id: str) -> str:
        """Return one graph's durability rather than the aggregate engine value."""
        return self._registry.durability_for(graph_id)

    def manager_for(self, graph_id: str) -> DriveManager:
        """Return the manager serving a graph, creating it when necessary."""
        return self._registry.manager_for(graph_id)

    def peek_manager(self, graph_id: str) -> DriveManager | None:
        """Return an existing graph manager without creating one."""
        return self._registry.peek(graph_id)

    async def start(self) -> None:
        """Idempotently recover split intents, then start every dispatcher."""
        if self._started:
            return
        session_dir = getattr(self._engine, "_session_dir", None)
        if session_dir is not None:
            await recover_split_intents(session_dir)
        self._started = True
        await self._registry.start_all()

    async def stop(self) -> None:
        """Drain reconciliations before stopping every graph manager."""
        await self._drain_reconcile()
        if self._started:
            await self._registry.stop_all()
            self._started = False

    async def attach_creature(self, creature: Any, env: Any) -> None:
        """Attach Drive services, storage, tools, and prompt state to a creature."""
        if env is not None:
            register_drive_service(env, self)
        if not self._started:
            session_dir = getattr(self._engine, "_session_dir", None)
            if session_dir is not None:
                await recover_split_intents(session_dir)
            self._started = True
            await self._registry.start_all()
        self._registry.manager_for(creature.graph_id)
        await install_drive_runtime(creature.agent, self)

    async def bind_graph_store(self, graph_id: str, store: Any) -> None:
        """Recover pending splits before binding a graph's persistent store."""
        path = getattr(store, "path", None)
        if path is not None:
            await recover_split_intents(Path(path).parent)
        await self._registry.bind_store(graph_id, store)

    async def drain_topology(self) -> None:
        """Apply pending Drive row movement from graph merges and splits."""
        await _topology.drain(self)

    async def detach_graph(self, graph_id: str) -> None:
        """Stop and forget a graph manager before its storage is replaced."""
        await self._drain_reconcile(graph_id)
        await self._registry.detach_and_stop(graph_id)

    def schedule_reconcile(self, creature: Any) -> None:
        """Schedule one reconciliation after a creature finishes restoration."""
        creature_id = creature.creature_id
        if creature_id in self._reconcile_creatures:
            return
        self._reconcile_creatures.add(creature_id)
        graph_id = creature.graph_id
        task = asyncio.ensure_future(self._reconcile_when_ready(creature))
        self._reconcile_tasks.setdefault(graph_id, set()).add(task)
        task.add_done_callback(
            lambda t, g=graph_id, c=creature_id: self._discard_reconcile(g, c, t)
        )

    def _discard_reconcile(
        self, graph_id: str, creature_id: str, task: asyncio.Task
    ) -> None:
        self._reconcile_creatures.discard(creature_id)
        group = self._reconcile_tasks.get(graph_id)
        if group is not None:
            group.discard(task)
            if not group:
                self._reconcile_tasks.pop(graph_id, None)

    async def _drain_reconcile(self, graph_id: str | None = None) -> None:
        """Cancel and await reconciliations for one graph or the whole runtime."""
        if graph_id is None:
            groups = list(self._reconcile_tasks.values())
            self._reconcile_tasks.clear()
        else:
            groups = [self._reconcile_tasks.pop(graph_id, set())]
        tasks = [t for group in groups for t in group if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _reconcile_when_ready(self, creature: Any) -> None:
        try:
            await creature.wait_restoration_ready()
        except asyncio.CancelledError:
            return
        if not creature.is_running:
            return
        session_dir = getattr(self._engine, "_session_dir", None)
        if session_dir is not None:
            await recover_split_intents(session_dir)
        # Topology changes can close the repository while this task waits on the
        # barrier, so a raced reconciliation must stop before manager startup.
        repo = self._registry.repository_for(creature.graph_id)
        if getattr(repo, "_closed", False):
            return
        # Dispatcher startup follows restoration to avoid contending with resume
        # writes; persisted assignees are remapped before redelivery.
        try:
            manager = await self._registry.ensure_started(creature.graph_id)
            await self._remap_resumed_assignees(creature.graph_id)
            await manager.reconcile(creature_id=creature.creature_id)
        except DriveRepositoryClosedError:
            return

    async def _remap_resumed_assignees(self, graph_id: str) -> None:
        """Remap persisted assignees to resumed creature IDs when unambiguous."""
        manager = self._registry.peek(graph_id)
        graph = self._engine._topology.graphs.get(graph_id)
        if manager is None or graph is None:
            return
        live_ids = set(graph.creature_ids)
        by_name: dict[str, list[str]] = {}
        for cid in live_ids:
            creature = self._engine._creatures.get(cid)
            if creature is None:
                continue
            by_name.setdefault(_clean_creature_name(creature.name), []).append(cid)
        # Filtering by the current graph ID would omit persisted rows that still
        # carry the pre-resume graph identity.
        for record in await manager.list_drives(DriveQuery(include_terminal=False)):
            assignment = await manager.get_assignment(record.drive_id)
            if assignment is None or assignment.assignee_creature_id is None:
                continue
            old_id = assignment.assignee_creature_id
            if old_id in live_ids:
                continue
            candidates = by_name.get(_decode_creature_name(old_id), [])
            if len(candidates) == 1:
                await manager.remap_assignee(
                    record.drive_id, candidates[0], graph_id=graph_id
                )
            else:
                # Missing or ambiguous assignees must be orphaned rather than
                # guessed, preserving explicit ownership semantics.
                await manager.orphan_and_block(
                    record, reason="resume_unresolved_assignee"
                )

    async def on_creature_stopped(self, creature_id: str) -> None:
        manager = self._manager_for_creature(creature_id)
        if manager is None:
            return
        try:
            await manager.on_creature_stopped(creature_id)
        except DriveRepositoryClosedError:
            # Repository closure is a quiescence signal, not an error: the
            # graph's drive repo may already be closed (Windows closes a
            # session-backed repo's sqlite connection with its store).
            return

    async def on_creature_removed(
        self,
        creature_id: str,
        *,
        graph_id: str | None = None,
        graph_member_ids: frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        manager = (
            self._registry.peek(graph_id)
            if graph_id is not None
            else self._manager_for_creature(creature_id)
        )
        if manager is None:
            return ()
        try:
            return await manager.on_creature_removed(
                creature_id, graph_member_ids=graph_member_ids
            )
        except DriveRepositoryClosedError:
            # Repository closure is a quiescence signal, not an error.
            return ()

    def _manager_for_creature(self, creature_id: str) -> DriveManager | None:
        creature = self._engine._creatures.get(creature_id)
        if creature is None:
            return None
        return self._registry.peek(creature.graph_id)

    def reconfigure(
        self,
        registrations: "list[DriveRegistration] | tuple[DriveRegistration, ...]",
    ) -> str:
        """Apply additive registry changes live and classify other outcomes."""
        try:
            new_snapshot = EnabledRegistrySnapshot.build(tuple(registrations))
        except Exception as exc:
            logger.warning("drive reconfigure rejected", error=str(exc))
            return REJECTED
        current = {e.descriptor.name for e in self._snapshot.entries}
        proposed = {e.descriptor.name for e in new_snapshot.entries}
        if current - proposed:
            self._emit_reconfigure_required(sorted(current - proposed))
            return RESTART_REQUIRED
        self._snapshot = new_snapshot
        self._registry.set_snapshot(new_snapshot)
        for creature in list(self._engine._creatures.values()):
            refresh_drive_prompt(creature.agent, new_snapshot)
        self._emit_engine_event(
            EventKind.DRIVE_REGISTRATION_CHANGED,
            None,
            {"enabled": sorted(proposed)},
        )
        return APPLIED_LIVE

    def _observer_for_graph(self, graph_id: str) -> DriveObserver:
        """Create an observer that tags events with their source graph."""
        return lambda obs: self._on_observation(obs, graph_id)

    def _on_observation(
        self, obs: DriveObservation, graph_id: str | None = None
    ) -> None:
        """Publish structural Drive observations as graph-scoped engine events."""
        try:
            kind = EventKind(obs.kind)
        except ValueError:
            if obs.kind not in _INTERNAL_OBSERVATION_KINDS:
                logger.warning(
                    "drive observation kind is neither a declared EventKind nor a "
                    "known internal signal; dropping it",
                    obs_kind=obs.kind,
                )
            return
        payload = dict(obs.payload)
        if obs.drive_id is not None:
            payload.setdefault("drive_id", obs.drive_id)
        self._emit_engine_event(kind, obs.drive_id, payload, graph_id)

    def _emit_reconfigure_required(self, disabled: list[str]) -> None:
        self._emit_engine_event(
            EventKind.DRIVE_RUNTIME_RECONFIGURE_REQUIRED,
            None,
            {"disabled": disabled},
        )

    def _emit_engine_event(
        self,
        kind: EventKind,
        drive_id: str | None,
        payload: dict[str, Any],
        graph_id: str | None = None,
    ) -> None:
        emit = getattr(self._engine, "_emit", None)
        if not callable(emit):
            return
        emit(EngineEvent(kind=kind, graph_id=graph_id, payload=payload))


__all__ = [
    "APPLIED_LIVE",
    "REJECTED",
    "RESTART_REQUIRED",
    "DriveRuntime",
    "build_drive_runtime",
]
