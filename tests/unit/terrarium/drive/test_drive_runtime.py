"""Unit tests for :mod:`terrarium.drive.runtime` — the engine-side glue.

Pins: ``build_drive_runtime`` validation; the engine sink's admit /
rejected-because-stopped decision + settlement mapping; the fairness probe;
observation -> EngineEvent mapping (mapped kinds surface, internal kinds drop);
reconfigure results (applied_live / restart_required / rejected); and lifecycle
delegation (creature removal orphans a creature-scoped drive).
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from kohakuterrarium.core.turn import TurnResult
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.errors import DriveValidationError
from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository
from kohakuterrarium.terrarium.drive.models import ActorRef, DriveStatus
from kohakuterrarium.terrarium.drive.registration import (
    DriveRegistrationDescriptor,
    GenericDriveRegistration,
)
from kohakuterrarium.terrarium.drive.requests import CreateDriveRequest
from kohakuterrarium.terrarium.drive.runtime import (
    APPLIED_LIVE,
    REJECTED,
    RESTART_REQUIRED,
    DriveRuntime,
    _EngineDriveSink,
    _result_to_settlement,
    build_drive_runtime,
)
from kohakuterrarium.terrarium.drive.sink import SettlementStatus
from kohakuterrarium.terrarium.events import EventKind

WORKER = ActorRef("creature", "worker")


class _Clock:
    def __init__(self):
        self.t = datetime(2026, 7, 11, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += timedelta(seconds=s)


class _FakeAgentState:
    def __init__(self):
        self._pending_mid_turn_inputs: list = []

    @property
    def has_pending_mid_turn_inputs(self) -> bool:
        # Mirror Agent.has_pending_mid_turn_inputs — the public probe the
        # sink reads (no private-field access).
        return bool(self._pending_mid_turn_inputs)


class _FakeCreature:
    def __init__(self, cid, *, running=True, result=None):
        self.creature_id = cid
        self._running = running
        self.agent = _FakeAgentState()
        self.result = result if result is not None else TurnResult(status="ok")
        self.injected: list = []

    @property
    def is_running(self):
        return self._running

    async def inject_event(self, event, *, correlation_id=None):
        self.injected.append((event, correlation_id))
        return self.result


class _FakeEngine:
    def __init__(self):
        self._creatures: dict = {}
        self.emitted: list = []

    def _emit(self, ev):
        self.emitted.append(ev)


class _AltRegistration:
    name = "zzz_alt"
    kind = "alt"
    schema_version = 1

    def descriptor(self):
        return DriveRegistrationDescriptor(
            name=self.name,
            kind=self.kind,
            schema_version=1,
            required_roles=frozenset({"spec"}),
        )

    def validate_spec(self, spec):
        return None


def _runtime(engine=None, registrations=None, clock=None):
    return DriveRuntime(
        engine or _FakeEngine(),
        DriveRuntimeConfig(enabled=True),
        tuple(registrations or default_registrations()),
        clock=clock,
    )


# ── build_drive_runtime ───────────────────────────────────────


class TestBuildDriveRuntime:
    def test_none_config_builds_nothing(self):
        assert build_drive_runtime(_FakeEngine(), None, (), None) is None

    def test_enabled_empty_registrations_raises(self):
        with pytest.raises(DriveValidationError):
            build_drive_runtime(
                _FakeEngine(), DriveRuntimeConfig(enabled=True), (), None
            )

    def test_disabled_config_builds_nothing(self):
        rt = build_drive_runtime(
            _FakeEngine(), DriveRuntimeConfig(enabled=False), (), None
        )
        assert rt is None

    def test_enabled_with_registrations_builds(self):
        rt = build_drive_runtime(
            _FakeEngine(),
            DriveRuntimeConfig(enabled=True),
            default_registrations(),
            None,
        )
        assert rt is not None
        assert rt.durability == "ephemeral"


# ── engine sink ───────────────────────────────────────────────


class TestEngineSink:
    async def test_rejected_when_creature_absent(self):
        engine = _FakeEngine()
        sink = _EngineDriveSink(engine)
        out = await sink.deliver("ghost", object(), delivery_id="d1")
        assert out.admitted is False

    async def test_rejected_when_creature_stopped(self):
        engine = _FakeEngine()
        engine._creatures["worker"] = _FakeCreature("worker", running=False)
        sink = _EngineDriveSink(engine)
        out = await sink.deliver("worker", object(), delivery_id="d1")
        assert out.admitted is False

    async def test_deferred_when_not_restoration_ready(self):
        # Barrier not crossed (§6.5): a running creature still defers — the
        # event must NOT enter the creature.
        engine = _FakeEngine()
        creature = _FakeCreature("worker")
        creature.restoration_ready = False
        engine._creatures["worker"] = creature
        sink = _EngineDriveSink(engine)
        out = await sink.deliver("worker", object(), delivery_id="d1")
        assert out.admitted is False
        assert creature.injected == []

    async def test_admits_and_settles_ok(self):
        engine = _FakeEngine()
        creature = _FakeCreature("worker", result=TurnResult(status="ok"))
        engine._creatures["worker"] = creature
        sink = _EngineDriveSink(engine)
        event = object()
        out = await sink.deliver("worker", event, delivery_id="d7")
        assert out.admitted is True
        settlement = await out.settlement()
        assert settlement.status is SettlementStatus.OK
        # The event + correlation id reached the creature.
        assert creature.injected[-1] == (event, "d7")

    async def test_has_queued_foreign_work(self):
        engine = _FakeEngine()
        creature = _FakeCreature("worker")
        engine._creatures["worker"] = creature
        sink = _EngineDriveSink(engine)
        assert sink.has_queued_foreign_work("worker") is False
        creature.agent._pending_mid_turn_inputs.append(object())
        assert sink.has_queued_foreign_work("worker") is True
        assert sink.has_queued_foreign_work("ghost") is False


class TestSettlementMapping:
    def test_ok(self):
        s = _result_to_settlement(TurnResult(status="ok", correlation_id="d1"))
        assert s.status is SettlementStatus.OK
        assert s.detail["correlation_id"] == "d1"

    def test_error(self):
        s = _result_to_settlement(TurnResult(status="error", error="boom"))
        assert s.status is SettlementStatus.ERROR
        assert s.detail["error"] == "boom"

    def test_timeout_is_error(self):
        s = _result_to_settlement(TurnResult(status="timeout"))
        assert s.status is SettlementStatus.ERROR

    def test_rejected_and_interrupted_are_transient(self):
        assert (
            _result_to_settlement(TurnResult(status="rejected")).status
            is SettlementStatus.INTERRUPTED
        )
        assert (
            _result_to_settlement(TurnResult(status="interrupted")).status
            is SettlementStatus.INTERRUPTED
        )


# ── observation -> EngineEvent ────────────────────────────────


class TestObservationMapping:
    def test_mapped_kind_emits_engine_event(self):
        from kohakuterrarium.terrarium.drive.sink import DriveObservation

        engine = _FakeEngine()
        rt = _runtime(engine=engine)
        rt._on_observation(
            DriveObservation(
                kind="drive_created", drive_id="D1", payload={"kind": "generic"}
            )
        )
        assert len(engine.emitted) == 1
        ev = engine.emitted[0]
        assert ev.kind is EventKind.DRIVE_CREATED
        assert ev.payload["drive_id"] == "D1"
        assert ev.payload["kind"] == "generic"

    def test_progress_and_proposal_pending_surface(self):
        # The frontend depends on both: progress live-updates and the proposal_id
        # for cross-actor approval. Both must reach subscribers as EngineEvents.
        from kohakuterrarium.terrarium.drive.sink import DriveObservation

        engine = _FakeEngine()
        rt = _runtime(engine=engine)
        rt._on_observation(
            DriveObservation(
                kind="drive_progress", drive_id="D1", payload={"progress_id": "p1"}
            )
        )
        rt._on_observation(
            DriveObservation(
                kind="drive_proposal_pending",
                drive_id="D1",
                payload={"proposal_id": "prop1", "target": "completed"},
            )
        )
        kinds = [e.kind for e in engine.emitted]
        assert EventKind.DRIVE_PROGRESS in kinds
        assert EventKind.DRIVE_PROPOSAL_PENDING in kinds
        pending = next(
            e for e in engine.emitted if e.kind is EventKind.DRIVE_PROPOSAL_PENDING
        )
        assert pending.payload["proposal_id"] == "prop1"

    def test_internal_signal_is_dropped_quietly(self, monkeypatch):
        from kohakuterrarium.terrarium.drive import runtime as rt_mod
        from kohakuterrarium.terrarium.drive.sink import DriveObservation

        engine = _FakeEngine()
        rt = _runtime(engine=engine)
        warnings: list = []
        monkeypatch.setattr(
            rt_mod.logger, "warning", lambda *a, **k: warnings.append(a)
        )
        # A known internal signal is dropped WITHOUT a warning (design §9.4).
        rt._on_observation(DriveObservation(kind="drive_backpressured", drive_id="D1"))
        assert engine.emitted == []
        assert warnings == []

    def test_unknown_kind_is_dropped_loudly(self, monkeypatch):
        # A kind that is neither declared nor a known internal signal is a bug (an
        # emit added without an EventKind); it is dropped but logged LOUDLY so it
        # cannot silently regress the way drive_proposal_pending did.
        from kohakuterrarium.terrarium.drive import runtime as rt_mod
        from kohakuterrarium.terrarium.drive.sink import DriveObservation

        engine = _FakeEngine()
        rt = _runtime(engine=engine)
        warnings: list = []
        monkeypatch.setattr(
            rt_mod.logger, "warning", lambda *a, **k: warnings.append(a)
        )
        rt._on_observation(
            DriveObservation(kind="drive_totally_unknown", drive_id="D1")
        )
        assert engine.emitted == []
        assert len(warnings) == 1


# ── observation graph identity (Defect 5) ─────────────────────


class TestObservationGraphIdentity:
    def test_per_graph_observer_stamps_graph_id(self):
        # The observer the runtime installs on graph gA's manager stamps gA on
        # every EngineEvent it emits, so graph-filtered subscribers can match.
        from kohakuterrarium.terrarium.drive.sink import DriveObservation

        engine = _FakeEngine()
        rt = _runtime(engine=engine)
        observer = rt.manager_for("gA")._observer
        observer(
            DriveObservation(
                kind="drive_created", drive_id="D1", payload={"kind": "generic"}
            )
        )
        observer(
            DriveObservation(
                kind="drive_ready", drive_id="D1", payload={"delivery_id": "x1"}
            )
        )
        observer(
            DriveObservation(
                kind="drive_proposal_pending",
                drive_id="D1",
                payload={"proposal_id": "p1"},
            )
        )
        observer(
            DriveObservation(
                kind="drive_progress", drive_id="D1", payload={"progress_id": "pr1"}
            )
        )
        assert {e.kind for e in engine.emitted} == {
            EventKind.DRIVE_CREATED,
            EventKind.DRIVE_READY,
            EventKind.DRIVE_PROPOSAL_PENDING,
            EventKind.DRIVE_PROGRESS,
        }
        assert all(e.graph_id == "gA" for e in engine.emitted)
        # Existing payload keys stay intact — only graph identity is added.
        created = next(e for e in engine.emitted if e.kind is EventKind.DRIVE_CREATED)
        assert created.payload["kind"] == "generic"
        assert created.payload["drive_id"] == "D1"

    def test_distinct_graphs_stamp_distinct_graph_ids(self):
        from kohakuterrarium.terrarium.drive.sink import DriveObservation

        engine = _FakeEngine()
        rt = _runtime(engine=engine)
        rt.manager_for("gA")._observer(
            DriveObservation(kind="drive_created", drive_id="A", payload={})
        )
        rt.manager_for("gB")._observer(
            DriveObservation(kind="drive_created", drive_id="B", payload={})
        )
        stamped = {e.payload["drive_id"]: e.graph_id for e in engine.emitted}
        assert stamped == {"A": "gA", "B": "gB"}

    async def test_real_create_drive_carries_graph_id(self):
        # End-to-end through the actual manager emit path (not a hand-fed
        # observation): a Drive created in gA emits drive_created stamped with gA.
        engine = _FakeEngine()
        rt = _runtime(engine=engine)
        manager = rt.manager_for("gA")
        await manager.create_drive(
            _creature_request("wa"),
            actor=ActorRef("creature", "wa"),
            graph_id="gA",
        )
        created = [e for e in engine.emitted if e.kind is EventKind.DRIVE_CREATED]
        assert created and created[0].graph_id == "gA"


# ── reconfigure ───────────────────────────────────────────────


class TestReconfigure:
    def test_add_registration_applied_live(self):
        engine = _FakeEngine()
        rt = _runtime(engine=engine, registrations=[GenericDriveRegistration()])
        result = rt.reconfigure([GenericDriveRegistration(), _AltRegistration()])
        assert result == APPLIED_LIVE
        assert "alt" in rt.snapshot.available_kinds()
        assert any(
            e.kind is EventKind.DRIVE_REGISTRATION_CHANGED for e in engine.emitted
        )

    def test_remove_registration_restart_required(self):
        engine = _FakeEngine()
        rt = _runtime(
            engine=engine,
            registrations=[GenericDriveRegistration(), _AltRegistration()],
        )
        result = rt.reconfigure([GenericDriveRegistration()])
        assert result == RESTART_REQUIRED
        # The RUNNING snapshot is unchanged — no half-applied state.
        assert "alt" in rt.snapshot.available_kinds()
        assert any(
            e.kind is EventKind.DRIVE_RUNTIME_RECONFIGURE_REQUIRED
            for e in engine.emitted
        )

    def test_invalid_registrations_rejected(self):
        engine = _FakeEngine()
        rt = _runtime(engine=engine, registrations=[GenericDriveRegistration()])
        # Two registrations claiming one name is a hard build error -> rejected.
        result = rt.reconfigure(
            [GenericDriveRegistration(), GenericDriveRegistration()]
        )
        assert result == REJECTED
        assert rt.snapshot.available_kinds() == frozenset({"generic"})


# ── lifecycle delegation ──────────────────────────────────────


def _creature_request(scope_id="worker"):
    return CreateDriveRequest(
        kind="generic",
        title="watch",
        scope_type="creature",
        scope_id=scope_id,
        owner=ActorRef("creature", scope_id),
        owner_scope="creature",
        created_by=ActorRef("creature", scope_id),
    )


class TestLifecycle:
    async def test_removal_orphans_creature_scoped_drive(self):
        clock = _Clock()
        rt = _runtime(clock=clock)
        # Phase F: the manager is resolved per graph, not one engine-wide.
        manager = rt.manager_for("g1")
        record = await manager.create_drive(
            _creature_request(), actor=WORKER, graph_id="g1"
        )
        affected = await rt.on_creature_removed("worker", graph_id="g1")
        assert record.drive_id in affected
        after = await manager.get_drive(record.drive_id)
        # A creature-scoped drive orphans-and-blocks — never silently reassigned.
        assert after.status is DriveStatus.BLOCKED
        assignment = await manager.get_assignment(record.drive_id)
        assert assignment.assignment_state == "orphaned"


# ── per-graph partitioning (Phase F, design §3.1) ─────────────


class TestPerGraph:
    async def test_two_graphs_have_isolated_repositories(self):
        rt = _runtime()
        await rt.start()
        mgr_a = rt.manager_for("gA")
        mgr_b = rt.manager_for("gB")
        # Distinct managers over distinct repositories.
        assert mgr_a is not mgr_b
        assert mgr_a.repository is not mgr_b.repository
        record = await mgr_a.create_drive(
            _creature_request("wa"), actor=ActorRef("creature", "wa"), graph_id="gA"
        )
        # A Drive created in graph A is invisible to graph B's manager (§3.1).
        assert await mgr_b.get_drive(record.drive_id) is None
        assert await mgr_a.get_drive(record.drive_id) is not None
        await rt.stop()

    async def test_manager_convenience_raises_when_ambiguous(self):
        rt = _runtime()
        rt.manager_for("gA")
        rt.manager_for("gB")
        # ``.manager`` is a single-graph convenience; two graphs is ambiguous.
        with pytest.raises(RuntimeError):
            _ = rt.manager
        # ``manager_for`` still resolves each explicitly.
        assert rt.manager_for("gA") is not rt.manager_for("gB")

    def test_peek_never_creates(self):
        rt = _runtime()
        assert rt.peek_manager("ghost") is None
        # A get-or-create call materialises it; peek then finds the same one.
        created = rt.manager_for("ghost")
        assert rt.peek_manager("ghost") is created


# ── start / stop ──────────────────────────────────────────────


class TestStartStop:
    async def test_start_idempotent_and_stop_drains(self):
        rt = _runtime()
        await rt.start()
        await rt.start()  # idempotent
        rt.manager_for("g1")
        await rt.registry.ensure_started("g1")
        assert rt.manager.dispatcher._task is not None
        await rt.stop()
        assert rt.manager.dispatcher._task is None
        await rt.stop()  # idempotent


class TestDropStopOrdering:
    async def test_detach_and_stop_awaits_stop_before_forgetting(self):
        # Ordering-side pin (companion to delivery's TestRepoCloseRace): a graph
        # DROPPED by a topology merge/split has its dispatcher AWAIT-stopped (not
        # fire-and-forget) while its repo is still open, so the store's companion
        # closer never shuts a live executor. The running dispatcher is fully
        # down (task cleared) by the time detach_and_stop returns.
        rt = _runtime()
        rt.manager_for("g1")
        await rt.registry.ensure_started("g1")
        manager = rt.registry.peek("g1")
        assert manager.dispatcher._task is not None  # running

        await rt.registry.detach_and_stop("g1")

        assert manager.dispatcher._task is None  # fully stopped, awaited
        assert rt.registry.peek("g1") is None  # forgotten
        await rt.stop()

    async def test_rebind_keeps_graph_and_swaps_repo(self):
        # A rebind (survivor/child keeps its store) swaps the repo in place and
        # leaves the graph live — the old dispatcher stop is fire-and-forget (the
        # caller already closed the old repo), so it must not block the swap.
        rt = _runtime()
        rt.manager_for("g1")
        await rt.registry.ensure_started("g1")
        old_manager = rt.registry.peek("g1")
        new_repo = MemoryDriveRepository()

        rt.registry.rebind_repository("g1", new_repo)

        assert rt.registry.repository_for("g1") is new_repo
        assert rt.registry.peek("g1") is not old_manager  # fresh manager
        await rt.stop()

    async def test_stop_all_drains_scheduled_stops_not_cancels(self):
        # drive-gaps ordering: a fire-and-forget stop from a topology drop must be
        # DRAINED by shutdown (awaited), not cancelled mid-round — a cancelled stop
        # leaves the detached dispatcher's _run racing a closing repo.
        rt = _runtime()
        rt.manager_for("g1")
        await rt.registry.ensure_started("g1")
        dropped = rt.registry.peek("g1")
        assert dropped.dispatcher._task is not None  # running

        rt.registry.drop_graph("g1")  # fire-and-forget stop scheduled
        assert rt.registry._stop_tasks  # a stop is in flight
        assert rt.registry.peek("g1") is None  # detached from _managers

        await rt.registry.stop_all()

        assert dropped.dispatcher._task is None  # drained to completion, not cancelled
        assert rt.registry._stop_tasks == set()
        await rt.stop()


class _BlockingCreature:
    """A creature whose restoration barrier never resolves until cancelled."""

    def __init__(self, cid, graph_id):
        self.creature_id = cid
        self.graph_id = graph_id
        self.is_running = True
        self.started = asyncio.Event()

    async def wait_restoration_ready(self):
        self.started.set()
        await asyncio.Event().wait()  # blocks until the task is cancelled


class TestReconcileDrain:
    async def test_stop_cancels_and_awaits_reconcile_tasks(self):
        # A reconcile task pending at teardown must be cancelled AND awaited so it
        # never calls a repository whose executor the store close is about to shut.
        rt = _runtime()
        creature = _BlockingCreature("c1", "g1")
        rt.schedule_reconcile(creature)
        await asyncio.wait_for(creature.started.wait(), timeout=2.0)
        assert rt._reconcile_tasks.get("g1")  # tracked per graph
        await rt.stop()
        assert rt._reconcile_tasks == {}  # fully drained, not just cancelled

    async def test_detach_graph_drains_only_that_graphs_tasks(self):
        rt = _runtime()
        c1 = _BlockingCreature("c1", "g1")
        c2 = _BlockingCreature("c2", "g2")
        rt.schedule_reconcile(c1)
        rt.schedule_reconcile(c2)
        await asyncio.wait_for(c1.started.wait(), timeout=2.0)
        await asyncio.wait_for(c2.started.wait(), timeout=2.0)
        await rt.detach_graph("g1")
        assert "g1" not in rt._reconcile_tasks  # g1 drained
        assert rt._reconcile_tasks.get("g2")  # g2 untouched
        await rt.stop()  # clean up the rest
        assert rt._reconcile_tasks == {}
