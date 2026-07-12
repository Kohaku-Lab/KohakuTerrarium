"""Behaviour contract for the Drive dispatcher (design §5, §6.1).

Delivery races, stale/duplicate suppression, retry-backoff schedule, dead-letter,
fairness, backpressure, recovery, and clean start/stop over the real
``MemoryDriveRepository`` + a scriptable ``FakeSink`` with injected clock/rng.
"""

import asyncio
import logging
from datetime import timedelta

from kohakuterrarium.terrarium.drive.config import DriveRetryConfig
from kohakuterrarium.terrarium.drive.delivery import RECOVERY_WARNING
from kohakuterrarium.terrarium.drive.manager import DriveManager
from kohakuterrarium.terrarium.drive.memory import MemoryDriveRepository
from kohakuterrarium.terrarium.drive.models import DriveStatus
from kohakuterrarium.terrarium.drive.registration import GenericDriveRegistration
from kohakuterrarium.terrarium.drive.requests import DrivePatch
from kohakuterrarium.terrarium.drive.sink import (
    DeliveryOutcome,
    Settlement,
    SettlementStatus,
)
from kohakuterrarium.terrarium.drive.snapshot import EnabledRegistrySnapshot
from kohakuterrarium.terrarium.drive.store import SqliteDriveRepository

from tests.unit.terrarium.drive._harness import (
    OTHER,
    WORKER,
    Clock,
    FakeSink,
    ScriptedRng,
    build_manager,
    creature_request,
    graph_request,
    ids,
    kinds,
    make_config,
)


async def _round(h) -> None:
    await h.manager.dispatcher.dispatch_once()
    await h.manager.dispatcher.drain()


async def _create_worker(h, **over) -> str:
    record = await h.manager.create_drive(
        creature_request(**over), actor=WORKER, graph_id="g1"
    )
    return record.drive_id


# ── happy path: one admission, one acknowledgement ────────────────────


class TestAdmitAcknowledge:
    async def test_create_delivers_once_and_acknowledges(self):
        h = build_manager()
        did = await _create_worker(h)
        await _round(h)
        deliveries = await h.repo.list_deliveries(did)
        assert len(deliveries) == 1
        assert deliveries[0].state == "acknowledged"
        assert len(h.sink.delivered) == 1
        assert h.sink.delivered[0].event.type == "drive_ready"
        assert h.sink.delivered[0].event.stackable is False
        assert "drive_delivery_admitted" in kinds(h.observations)
        assert "drive_delivery_acknowledged" in kinds(h.observations)

    async def test_repeated_rounds_do_not_re_deliver_a_settled_drive(self):
        h = build_manager()
        did = await _create_worker(h)
        await _round(h)
        await _round(h)  # claim again — nothing new should be admitted
        assert len(h.sink.delivered) == 1
        assert len(await h.repo.list_deliveries(did)) == 1

    async def test_event_context_carries_framework_identity(self):
        h = build_manager()
        did = await _create_worker(h)
        await _round(h)
        ctx = h.sink.delivered[0].event.context
        assert ctx["drive_id"] == did
        assert ctx["drive_kind"] == "generic"
        assert ctx["drive_revision"] == 1
        assert ctx["lifecycle_epoch"] == 0
        assert ctx["delivery_reason"] == "activated"
        assert "delivery_id" in ctx and "assignment_id" in ctx


# ── stopped assignee: deferred, no failure count ──────────────────────


class TestStoppedAssignee:
    async def test_stopped_defers_without_failure_count(self):
        h = build_manager()
        h.sink.stopped.add("worker")
        did = await _create_worker(h)
        await _round(h)
        delivery = (await h.repo.list_deliveries(did))[0]
        assert delivery.state == "pending"
        assert delivery.attempt == 0  # deferral is not a failure (§6.1)
        assert delivery.acknowledged_at is None
        assert "drive_delivery_deferred" in kinds(h.observations)

    async def test_delivers_after_assignee_comes_back(self):
        h = build_manager()
        h.sink.stopped.add("worker")
        did = await _create_worker(h)
        await _round(h)
        h.sink.stopped.discard("worker")
        await _round(h)
        assert (await h.repo.list_deliveries(did))[0].state == "acknowledged"


# ── stale / duplicate suppression ─────────────────────────────────────


class TestStaleSuppression:
    async def test_revision_bump_supersedes_pending_delivery(self):
        h = build_manager()
        did = await _create_worker(h)
        await h.manager.update_drive(
            did, DrivePatch(title="v2"), expected_revision=1, actor=WORKER
        )
        await _round(h)
        assert h.sink.delivered == []
        assert (await h.repo.list_deliveries(did))[0].state == "superseded"

    async def test_reassign_epoch_fences_old_delivery(self):
        h = build_manager()
        rec = await h.manager.create_drive(
            graph_request(), actor=WORKER, graph_id="g1", is_privileged=True
        )
        did = rec.drive_id
        old = (await h.repo.list_deliveries(did))[0]
        await h.manager.reassign(
            did, "other", "g1", expected_revision=1, actor=WORKER, is_privileged=True
        )
        states = {d.delivery_id: d.state for d in await h.repo.list_deliveries(did)}
        assert states[old.delivery_id] == "superseded"
        await _round(h)
        assert {d.creature_id for d in h.sink.delivered} == {"other"}

    async def test_duplicate_logical_send_yields_one_admission(self):
        h = build_manager()
        did = await _create_worker(h)
        # a second physical row with the SAME logical key (revision/epoch/gen)
        await h.repo.enqueue_delivery(did, reason="ready")
        await _round(h)
        states = sorted(d.state for d in await h.repo.list_deliveries(did))
        assert states.count("superseded") == 1
        assert len(h.sink.delivered) == 1


# ── retry / backoff / dead-letter ─────────────────────────────────────


class TestRetry:
    async def test_backoff_schedule_and_dead_letter(self):
        clock = Clock()
        t0 = clock()
        h = build_manager(
            clock=clock,
            sink=FakeSink(default=SettlementStatus.ERROR),
            rng=ScriptedRng((0.5,)),  # no jitter -> exact backoff
            config=make_config(
                retry=DriveRetryConfig(max_attempts=3, initial_backoff_s=2)
            ),
        )
        did = await _create_worker(h)

        await _round(h)
        d1 = (await h.repo.list_deliveries(did))[0]
        assert d1.state == "retry_wait" and d1.attempt == 1
        assert d1.available_at == t0 + timedelta(seconds=2)

        clock.advance(2)
        await _round(h)
        d2 = (await h.repo.list_deliveries(did))[0]
        assert d2.state == "retry_wait" and d2.attempt == 2
        assert d2.available_at == t0 + timedelta(seconds=6)  # +2 then +4

        clock.advance(4)
        await _round(h)
        d3 = (await h.repo.list_deliveries(did))[0]
        assert d3.state == "dead_letter"
        assert (await h.repo.get(did)).status is DriveStatus.BLOCKED  # §5.6 default
        outbox = [e.kind for e in await h.repo.list_outbox(include_dispatched=True)]
        assert "drive_dead_lettered" in outbox
        assert "drive_delivery_dead_lettered" in kinds(h.observations)

    async def test_sink_exception_is_transient_retry(self):
        h = build_manager()
        h.sink.raise_on.add("worker")
        did = await _create_worker(h)
        await _round(h)
        delivery = (await h.repo.list_deliveries(did))[0]
        assert delivery.state == "retry_wait" and delivery.attempt == 1

    async def test_settled_error_never_auto_completes(self):
        h = build_manager(sink=FakeSink(default=SettlementStatus.ERROR))
        did = await _create_worker(h)
        await _round(h)
        assert (await h.repo.get(did)).status is DriveStatus.ACTIVE


# ── fairness / scheduler order ────────────────────────────────────────


class TestFairness:
    async def test_one_admission_per_assignee_per_round(self):
        h = build_manager()
        await _create_worker(h, title="a")
        await _create_worker(h, title="b")
        await h.manager.dispatcher.dispatch_once()
        assert len(h.sink.delivered) == 1
        await h.manager.dispatcher.drain()
        await _round(h)
        assert len(h.sink.delivered) == 2

    async def test_scheduler_prefers_higher_priority(self):
        h = build_manager()
        await _create_worker(h, title="low", priority=0)
        high = await _create_worker(h, title="high", priority=10)
        await h.manager.dispatcher.dispatch_once()
        assert h.sink.delivered[0].event.context["drive_id"] == high

    async def test_max_consecutive_yields_to_foreign_work(self):
        h = build_manager(config=make_config(max_consecutive_drive_turns=1))
        await _create_worker(h, title="a")
        await _create_worker(h, title="b")
        await _round(h)  # first admitted -> consecutive == 1
        assert len(h.sink.delivered) == 1
        h.sink.foreign.add("worker")
        await h.manager.dispatcher.dispatch_once()  # should yield, not admit
        assert len(h.sink.delivered) == 1
        assert "drive_yielded" in kinds(h.observations)
        h.sink.foreign.discard("worker")
        await _round(h)
        assert len(h.sink.delivered) == 2


# ── backpressure (dispatch side) ──────────────────────────────────────


class TestBackpressure:
    async def test_concurrency_limit_is_observable(self):
        gate = asyncio.Event()
        h = build_manager(config=make_config(dispatcher_concurrency=1))
        h.sink.gate = gate
        await _create_worker(h, title="w")
        await h.manager.create_drive(
            creature_request(
                title="o", scope_id="other", owner=OTHER, created_by=OTHER
            ),
            actor=OTHER,
            graph_id="g1",
        )
        await h.manager.dispatcher.dispatch_once()  # one admitted (gated), one blocked
        assert len(h.sink.delivered) == 1
        assert "drive_backpressured" in kinds(h.observations)
        gate.set()
        await h.manager.dispatcher.drain()
        await _round(h)
        assert len(h.sink.delivered) == 2


# ── recovery projection ───────────────────────────────────────────────


class TestRecovery:
    async def test_recovery_event_carries_uncertain_warning(self):
        h = build_manager()
        h.sink.no_settle = True  # admit but never acknowledge -> uncertain
        did = await _create_worker(h)
        await _round(h)
        assert (await h.repo.list_deliveries(did))[0].state == "admitted"
        h.sink.no_settle = False
        await h.manager.reconcile(creature_id="worker")
        await _round(h)
        recovery = [d for d in h.sink.delivered if d.event.type == "drive_recovery"]
        assert len(recovery) == 1
        assert RECOVERY_WARNING in recovery[0].event.prompt_override


# ── projection failure fails closed ───────────────────────────────────


class TestProjectionFailure:
    async def test_projection_error_retries_not_delivered(self):
        class BadProjection(GenericDriveRegistration):
            def project_event(self, drive, assignment, reason):
                raise RuntimeError("projection boom")

        snap = EnabledRegistrySnapshot.build([BadProjection()])
        h = build_manager(snapshot=snap)
        did = await _create_worker(h)
        await _round(h)
        delivery = (await h.repo.list_deliveries(did))[0]
        assert h.sink.delivered == []  # never reached the sink
        assert delivery.state == "retry_wait"


# ── start / stop lifecycle ────────────────────────────────────────────


class TestLifecycle:
    async def test_start_and_stop_are_idempotent(self):
        h = build_manager()
        await h.manager.start()
        await h.manager.start()  # no error
        assert h.manager.dispatcher._task is not None
        await h.manager.stop()
        await h.manager.stop()  # no error
        assert h.manager.dispatcher._task is None

    async def test_stop_releases_claims_and_leases(self):
        h = build_manager()
        did = await _create_worker(h)
        claimed = await h.repo.claim_deliveries(
            "drive-dispatcher", 10, lease_seconds=30
        )
        assert len(claimed) == 1
        assert (await h.repo.get_assignment(did)).lease_owner == "drive-dispatcher"
        await h.manager.stop()
        assignment = await h.repo.get_assignment(did)
        assert assignment.lease_owner is None
        assert (await h.repo.list_deliveries(did))[0].state == "pending"
        again = await h.repo.claim_deliveries("other-disp", 10, lease_seconds=30)
        assert len(again) == 1

    async def test_stop_release_survives_sqlite_reopen(self, tmp_path):
        clock = Clock()
        path = str(tmp_path / "d.sqlite")
        repo = SqliteDriveRepository(path, clock=clock, id_factory=ids("r"))
        manager = DriveManager(
            repo,
            EnabledRegistrySnapshot.build([GenericDriveRegistration()]),
            make_config(),
            FakeSink(),
            clock=clock,
            rng=ScriptedRng(),
            id_factory=ids("m"),
        )
        rec = await manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        did = rec.drive_id
        await repo.claim_deliveries("drive-dispatcher", 10, lease_seconds=30)
        await manager.stop()  # release is committed durably before we close
        repo.close_blocking()

        reopened = SqliteDriveRepository(path, clock=clock, id_factory=ids("r2"))
        try:
            assert (await reopened.get_assignment(did)).lease_owner is None
            assert (await reopened.list_deliveries(did))[0].state == "pending"
            again = await reopened.claim_deliveries("fresh", 10, lease_seconds=30)
            assert len(again) == 1
        finally:
            reopened.close_blocking()


# ── observer failure is fail-open ─────────────────────────────────────


class TestObserverFailure:
    async def test_raising_observer_does_not_break_delivery(self):
        clock = Clock()
        repo = MemoryDriveRepository(clock=clock, id_factory=ids("r"))
        sink = FakeSink()

        def boom(_obs):
            raise RuntimeError("observer boom")

        manager = DriveManager(
            repo,
            EnabledRegistrySnapshot.build([GenericDriveRegistration()]),
            make_config(),
            sink,
            observer=boom,
            clock=clock,
            rng=ScriptedRng(),
            id_factory=ids("m"),
        )
        record = await manager.create_drive(
            creature_request(), actor=WORKER, graph_id="g1"
        )
        await manager.dispatcher.dispatch_once()
        await manager.dispatcher.drain()
        assert (await repo.list_deliveries(record.drive_id))[0].state == "acknowledged"


# ── repository closed underneath a running round (design §6.4) ────────


class _GateDeliverSink:
    """Blocks INSIDE ``deliver`` so a dispatch round stays in-flight until the
    gate opens — lets a test close the repo while a round is mid-flight."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.in_deliver = asyncio.Event()

    async def deliver(self, creature_id, event, *, delivery_id):
        self.in_deliver.set()
        await self.gate.wait()

        async def _settle() -> Settlement:
            return Settlement(SettlementStatus.OK)

        return DeliveryOutcome.accepted(_settle)

    def has_queued_foreign_work(self, creature_id) -> bool:
        return False


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class TestRepoCloseRace:
    async def test_repo_close_mid_round_quiesces_cleanly(self, tmp_path):
        # A store close that shuts the repo executor while a dispatch round is
        # in flight must quiesce the dispatcher — ZERO "dispatch round failed"
        # logs and no crash in stop() — not spin against the dead executor.
        clock = Clock()
        repo = SqliteDriveRepository(
            str(tmp_path / "d.sqlite"), clock=clock, id_factory=ids("r")
        )
        sink = _GateDeliverSink()
        manager = DriveManager(
            repo,
            EnabledRegistrySnapshot.build([GenericDriveRegistration()]),
            make_config(),
            sink,
            clock=clock,
            rng=ScriptedRng(),
            id_factory=ids("m"),
            scan_interval=0.02,
        )
        capture = _Capture()
        lg = logging.getLogger("kohakuterrarium.terrarium.drive.delivery")
        lg.addHandler(capture)
        lg.setLevel(logging.ERROR)
        try:
            await manager.create_drive(creature_request(), actor=WORKER, graph_id="g1")
            await manager.start()
            # A round is now in flight, blocked inside the sink's deliver().
            await asyncio.wait_for(sink.in_deliver.wait(), timeout=5)
            repo.close_blocking()  # companion closer shuts the executor
            sink.gate.set()  # the in-flight round resumes -> repo call on a dead repo
            await manager.stop()  # must not raise
        finally:
            lg.removeHandler(capture)
            repo.close_blocking()
        assert [m for m in capture.messages if "dispatch round failed" in m] == []

    async def test_stop_awaits_in_flight_round_before_returning(self, tmp_path):
        # A clean stop() must fully drain the in-flight round — its repo calls
        # included — before returning, so a companion closer that runs after a
        # clean stop never races a live round (design §6.4). Here the round is
        # held mid-deliver; releasing the gate lets the awaited stop() drain it.
        clock = Clock()
        repo = SqliteDriveRepository(
            str(tmp_path / "d.sqlite"), clock=clock, id_factory=ids("r")
        )
        sink = _GateDeliverSink()
        manager = DriveManager(
            repo,
            EnabledRegistrySnapshot.build([GenericDriveRegistration()]),
            make_config(),
            sink,
            clock=clock,
            rng=ScriptedRng(),
            id_factory=ids("m"),
            scan_interval=0.02,
        )
        try:
            rec = await manager.create_drive(
                creature_request(), actor=WORKER, graph_id="g1"
            )
            await manager.start()
            await asyncio.wait_for(sink.in_deliver.wait(), timeout=5)
            # Open the gate concurrently so the awaited stop() drains the round.
            opener = asyncio.ensure_future(_set_soon(sink.gate))
            await manager.stop()
            await opener
            # The round settled on the LIVE repo and its dispatcher lease is gone.
            deliveries = await repo.list_deliveries(rec.drive_id)
            assert deliveries[0].state in ("admitted", "acknowledged")
            assert (await repo.get_assignment(rec.drive_id)).lease_owner is None
        finally:
            repo.close_blocking()


async def _set_soon(event: asyncio.Event) -> None:
    event.set()
