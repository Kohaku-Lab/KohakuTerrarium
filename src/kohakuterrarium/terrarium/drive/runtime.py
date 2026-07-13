"""Engine-side glue binding per-graph :class:`DriveManager`\\ s to real creatures.

:class:`DriveRuntime` is what a Drive-enabled :class:`Terrarium` owns. Phase E
ran a single manager over a single repository; Phase F partitions it: a
:class:`~kohakuterrarium.terrarium.drive.partition.GraphDriveRegistry` holds one
repository + manager per graph (design §3.1, §7.1), so a Drive created in graph A
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

# Reconfigure results (design §8.6).
APPLIED_LIVE = "applied_live"
RESTART_REQUIRED = "restart_required"
REJECTED = "rejected"

# Delivery-plane / manager observation kinds that are DELIBERATELY not surfaced on
# the engine bus (design §9.4): finer internal signals no subscriber consumes. A
# kind that is neither a declared EventKind nor listed here is treated as a bug
# (an emit added without an EventKind) and logged loudly by _on_observation.
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
    """Construct the engine's Drive runtime, or ``None`` when disabled.

    Omitted config and registrations select the default-on runtime. Explicitly
    passing ``DriveRuntimeConfig(enabled=False)`` builds nothing; an explicitly
    empty registration collection remains invalid for an enabled runtime."""
    config = drive_config if drive_config is not None else DriveRuntimeConfig()
    registrations = (
        default_registrations() if drive_registrations is None else drive_registrations
    )
    validate_runtime_selection(config, registrations)
    if not config.enabled:
        return None
    return DriveRuntime(engine, config, tuple(registrations), store=drive_store)


def _result_to_settlement(result: Any) -> Settlement:
    """Map a :class:`TurnResult` to a Drive :class:`Settlement` (§5.2)."""
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
        # A raced stop / interrupt is transient — the dispatcher defers/retries
        # rather than counting a hard turn failure.
        return Settlement(SettlementStatus.INTERRUPTED, detail)
    detail["error"] = getattr(result, "error", None) or status
    return Settlement(SettlementStatus.ERROR, detail)


class _EngineDriveSink:
    """:class:`DriveDeliverySink` over ``Creature.inject_event`` (design §5.2).

    ``deliver`` rejects-because-stopped when the target creature is absent, not
    running, or has not yet crossed its restoration barrier (§6.5: no Drive is
    delivered before restoration-ready); otherwise it admits the event by
    starting its turn and returns a settlement source that resolves when the
    turn settles. One sink serves every per-graph manager — it resolves the
    target creature by id engine-wide.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def deliver(
        self, creature_id: str, event: Any, *, delivery_id: str
    ) -> DeliveryOutcome:
        creature = self._engine._creatures.get(creature_id)
        if creature is None or not creature.is_running:
            return DeliveryOutcome.rejected_stopped()
        # Restoration barrier (§6.5): defer — with no failure count, same as a
        # stopped assignee — until the creature's startup trigger has settled.
        if not getattr(creature, "restoration_ready", True):
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
    """A :class:`GraphDriveRegistry` that binds each per-graph manager's
    observer to that manager's ``graph_id``.

    The base builds every manager with one shared observer, so a Drive
    observation reached the engine bus with no graph identity — graph-filtered
    subscribers could not match it and Drive events leaked across graphs.
    Binding the observer at the single manager-build choke point stamps the
    right ``graph_id`` on every emitted :class:`EngineEvent`."""

    def __init__(
        self, *, observer_factory: Callable[[str], DriveObserver], **kwargs: Any
    ) -> None:
        super().__init__(observer=None, **kwargs)
        self._observer_factory = observer_factory

    def _build_manager(self, graph_id: str, repo: Any) -> DriveManager:
        # ``self._observer`` is read only by the base build below; bind it to
        # this graph for that read, then restore so a later build rebinds clean.
        prev = self._observer
        self._observer = self._observer_factory(graph_id)
        try:
            return super()._build_manager(graph_id, repo)
        finally:
            self._observer = prev


class DriveRuntime:
    """The Drive facility a Drive-enabled engine owns (see module docstring)."""

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
        # graph_id -> in-flight reconcile tasks, so a graph's tasks can be
        # drained before its repository/store is closed (design §6.4/§6.5).
        self._reconcile_tasks: dict[str, set[asyncio.Task]] = {}
        # creature_ids with a pending barrier-gated reconcile — makes
        # ``schedule_reconcile`` idempotent per creature so a second call before
        # the first settles (e.g. a re-add) never schedules two tasks for one.
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

    # -- read surface --------------------------------------------------------

    @property
    def manager(self) -> DriveManager:
        """The sole per-graph manager (convenience for single-graph engines).

        Raises when zero or several graphs have a manager — a multi-graph caller
        must resolve the graph explicitly via :meth:`manager_for`."""
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
        """This graph's Drive durability (design §7.1, R1-41). Consumers that
        report per-record/per-graph durability MUST use this, not the aggregate
        :attr:`durability`, which is ``mixed`` for a mixed engine."""
        return self._registry.durability_for(graph_id)

    def manager_for(self, graph_id: str) -> DriveManager:
        """The manager serving ``graph_id`` (get-or-create, design §3.1)."""
        return self._registry.manager_for(graph_id)

    def peek_manager(self, graph_id: str) -> DriveManager | None:
        """The manager serving ``graph_id`` if one exists — never creates it."""
        return self._registry.peek(graph_id)

    # -- lifecycle -----------------------------------------------------------

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
        """Drain reconcile tasks, then drain + stop every manager (§6.4).

        Reconcile tasks are cancelled AND awaited so none is mid-flight against a
        repository whose executor the owned-store close is about to shut (that
        race leaked ``RuntimeError: cannot schedule new futures after shutdown``)."""
        await self._drain_reconcile()
        if self._started:
            await self._registry.stop_all()
            self._started = False

    async def attach_creature(self, creature: Any, env: Any) -> None:
        """Register the Drive service on the graph env, mint the graph's manager
        (repo bound, dispatcher NOT started), and install the self-service tools +
        prompt onto the creature's agent (idempotent; also the adopt/elevate path).

        The dispatcher is deliberately NOT started here: on the resume/adopt path
        this runs before the restoration barrier, so starting it would let it
        claim/write on the session file during state injection (design §6.5). Each
        creature-start site (``add_creature``, ``engine.start``, resume, recipe
        apply) calls ``schedule_reconcile`` after the creature starts, and the
        barrier's ``schedule_reconcile`` -> ``ensure_started`` starts it once the
        creature is restoration-ready."""
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
        """Recover pending splits, then bind before restoration-ready."""
        path = getattr(store, "path", None)
        if path is not None:
            await recover_split_intents(Path(path).parent)
        await self._registry.bind_store(graph_id, store)

    async def drain_topology(self) -> None:
        """Apply any pending merge/split Drive row movement (design §6.6-6.7).

        The sync coordinator (session_coord / channel_lifecycle) stashes the
        capture; the engine's async topology method calls this afterwards."""
        await _topology.drain(self)

    async def detach_graph(self, graph_id: str) -> None:
        """Cleanly stop + forget a graph's Drive manager before its session
        store (and companion Drive repo) is replaced/closed.

        Drains the graph's reconcile tasks first so none calls the repository
        after the store close shuts its executor (design §6.4/§7.1)."""
        await self._drain_reconcile(graph_id)
        await self._registry.detach_and_stop(graph_id)

    def schedule_reconcile(self, creature: Any) -> None:
        """Start the graph's dispatcher and reconcile this creature's Drives once
        it crosses the restoration barrier (design §6.5) — never before.

        Called from every creature-start site (add_creature / engine.start /
        resume / recipe apply). Idempotent per creature: a second call while a
        task is still pending is a no-op, so overlapping start paths never
        double-schedule."""
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
        """Cancel + await reconcile tasks (a graph's, or all) so none is left
        in flight against a repository whose executor is about to shut."""
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
        # A topology split/merge (or store replacement) can tear the graph's
        # repository down between scheduling and here. ``drain`` cancels this
        # task before that happens on the normal path; this pre-check + guard
        # keeps a raced task on any other path from starting a manager on a
        # closed executor (prevented, not merely logged, design §6.4).
        repo = self._registry.repository_for(creature.graph_id)
        if getattr(repo, "_closed", False):
            return
        # Barrier crossed: NOW start the graph's dispatcher (deferred from
        # attach/bind so it never contends with resume writes, design §6.5), then
        # remap any persisted assignee whose runtime id was re-minted on resume
        # (R1-43), and reconcile so persisted Drives redeliver.
        try:
            manager = await self._registry.ensure_started(creature.graph_id)
            await self._remap_resumed_assignees(creature.graph_id)
            await manager.reconcile(creature_id=creature.creature_id)
        except DriveRepositoryClosedError:
            return

    async def _remap_resumed_assignees(self, graph_id: str) -> None:
        """Restore persisted Drive assignments after a cold resume re-minted
        creature ids (design §6.5, R1-43).

        A resumed creature gets a fresh ``<name>_<random>`` id, so a persisted
        assignment/scope still names the OLD id and reconciliation for the new id
        would silently never redeliver. Match each stale assignee to a resumed
        creature by the name encoded in its old id: a UNIQUE match is remapped
        (audited); an AMBIGUOUS or MISSING match is explicitly orphaned/blocked,
        never silently chosen. Idempotent — a subsequent pass sees a live
        assignee and skips it."""
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
        # The manager is already graph-scoped, so list ALL its Drives — a
        # persisted graph-scoped record still names the OLD graph id, so a
        # graph_id-filtered query would miss exactly the rows to remap.
        for record in await manager.list_drives(DriveQuery(include_terminal=False)):
            assignment = await manager.get_assignment(record.drive_id)
            if assignment is None or assignment.assignee_creature_id is None:
                continue
            old_id = assignment.assignee_creature_id
            if old_id in live_ids:
                continue  # already a current id — not a stale re-mint
            candidates = by_name.get(_decode_creature_name(old_id), [])
            if len(candidates) == 1:
                await manager.remap_assignee(
                    record.drive_id, candidates[0], graph_id=graph_id
                )
            else:
                # 0 (assignee gone) or >1 (ambiguous name): explicit orphaned
                # state, never a silent guess (R1-43 / §16 invariant 8).
                await manager.orphan_and_block(
                    record, reason="resume_unresolved_assignee"
                )

    async def on_creature_stopped(self, creature_id: str) -> None:
        manager = self._manager_for_creature(creature_id)
        if manager is not None:
            await manager.on_creature_stopped(creature_id)

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
        return await manager.on_creature_removed(
            creature_id, graph_member_ids=graph_member_ids
        )

    def _manager_for_creature(self, creature_id: str) -> DriveManager | None:
        creature = self._engine._creatures.get(creature_id)
        if creature is None:
            return None
        return self._registry.peek(creature.graph_id)

    # -- reconfigure (design §8.6) -------------------------------------------

    def reconfigure(
        self,
        registrations: "list[DriveRegistration] | tuple[DriveRegistration, ...]",
    ) -> str:
        """Apply a registry change and return the outcome string.

        Enabling/adding a registration is applied live (snapshot swap on every
        graph's manager + prompt refresh). Disabling/removing one returns
        ``restart_required``. Invalid input is ``rejected`` — running registry
        unchanged in both cases."""
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

    # -- observation -> EngineEvent ------------------------------------------

    def _observer_for_graph(self, graph_id: str) -> DriveObserver:
        """A graph-scoped observer: the manager for ``graph_id`` uses this, so
        every observation it emits carries ``graph_id`` on its EngineEvent."""
        return lambda obs: self._on_observation(obs, graph_id)

    def _on_observation(
        self, obs: DriveObservation, graph_id: str | None = None
    ) -> None:
        """Map a structural Drive observation to an EngineEvent (design §9.4).

        A declared structural kind surfaces to ``Terrarium.subscribe`` consumers
        (the frontend/TUI). The delivery plane's finer internal signals
        (:data:`_INTERNAL_OBSERVATION_KINDS`) are deliberately dropped. A kind that
        is NEITHER declared NOR a known internal signal is a bug — an emit added
        without an EventKind — and is logged loudly rather than silently swallowed
        (R1: dropped structural events). Payloads carry ids/status/reason — never
        the full spec. ``graph_id`` is the emitting manager's graph so graph-
        filtered subscribers can match and events stay isolated per graph."""
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
